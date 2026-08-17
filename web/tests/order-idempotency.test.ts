import { afterEach, describe, expect, it, vi } from "vitest";
import {
  runIdempotentOrder,
  contentKey,
  markIndeterminatePlacement,
  IndeterminatePlacementError,
  CONTENT_HASH_TTL_MS,
  INDETERMINATE_RETENTION_MS,
  __resetOrderIdempotency,
} from "@/lib/orders/orderIdempotency";

afterEach(() => {
  __resetOrderIdempotency();
  vi.useRealTimers();
});

describe("runIdempotentOrder", () => {
  it("dedups CONCURRENT identical calls — placement runs once, duplicate flagged", async () => {
    let resolvePlacement: (v: string) => void = () => {};
    const placement = vi.fn(
      () => new Promise<string>((r) => (resolvePlacement = r)),
    );

    const first = runIdempotentOrder("k", 1000, placement); // in-flight
    const second = runIdempotentOrder("k", 1000, placement); // awaits the same
    resolvePlacement("ORDER-RESULT");
    const [a, b] = await Promise.all([first, second]);

    expect(placement).toHaveBeenCalledTimes(1);
    expect(a).toEqual({ value: "ORDER-RESULT", deduplicated: false });
    expect(b).toEqual({ value: "ORDER-RESULT", deduplicated: true });
  });

  it("treats DISTINCT keys independently", async () => {
    const placement = vi.fn(async () => "x");
    await runIdempotentOrder("a", 1000, placement);
    await runIdempotentOrder("b", 1000, placement);
    expect(placement).toHaveBeenCalledTimes(2);
  });

  it("dedups a repeat within the TTL after completion", async () => {
    const placement = vi.fn(async () => "ok");
    const a = await runIdempotentOrder("k", 10_000, placement);
    const b = await runIdempotentOrder("k", 10_000, placement);
    expect(placement).toHaveBeenCalledTimes(1);
    expect(a.deduplicated).toBe(false);
    expect(b.deduplicated).toBe(true);
  });

  it("re-runs after the TTL expires", async () => {
    vi.useFakeTimers();
    const placement = vi.fn(async () => "ok");
    await runIdempotentOrder("k", 1_000, placement);
    vi.advanceTimersByTime(1_500); // past TTL
    await runIdempotentOrder("k", 1_000, placement);
    expect(placement).toHaveBeenCalledTimes(2);
  });

  it("clears the key on failure so a genuine retry re-attempts", async () => {
    const placement = vi
      .fn()
      .mockRejectedValueOnce(new Error("IB infra blip"))
      .mockResolvedValueOnce("ok");

    await expect(runIdempotentOrder("k", 10_000, placement)).rejects.toThrow(
      "IB infra blip",
    );
    const retry = await runIdempotentOrder("k", 10_000, placement);
    expect(placement).toHaveBeenCalledTimes(2);
    expect(retry).toEqual({ value: "ok", deduplicated: false });
  });

  it("propagates the original rejection to a concurrent duplicate", async () => {
    let rejectPlacement: (e: Error) => void = () => {};
    const placement = vi.fn(
      () => new Promise((_res, rej) => (rejectPlacement = rej)),
    );
    const first = runIdempotentOrder("k", 1000, placement);
    const second = runIdempotentOrder("k", 1000, placement);
    rejectPlacement(new Error("rejected"));
    await expect(first).rejects.toThrow("rejected");
    await expect(second).rejects.toThrow("rejected");
    expect(placement).toHaveBeenCalledTimes(1);
  });
});

describe("contentKey", () => {
  it("is order-independent over object keys", () => {
    expect(contentKey({ a: 1, b: 2 })).toBe(contentKey({ b: 2, a: 1 }));
  });

  it("differs when any field differs", () => {
    expect(contentKey({ symbol: "PLTR", qty: 100 })).not.toBe(
      contentKey({ symbol: "PLTR", qty: 200 }),
    );
  });
});

/**
 * T-021 — the ambiguity window. `radonFetch` aborts the placement at 30s while
 * FastAPI's own script budget is 25s, so the abort fires AFTER the request
 * reached the upstream and IB may already hold a live order. Clearing the key on
 * that rejection let an identical operator retry re-place a live order.
 */
describe("runIdempotentOrder — indeterminate (timeout-class) placement", () => {
  const timeoutError = () =>
    new DOMException("The operation was aborted due to timeout", "TimeoutError");

  /** Resolve with the rejection reason; fail loudly if the promise fulfils. */
  function rejection(promise: Promise<unknown>): Promise<unknown> {
    return promise.then(
      () => {
        throw new Error("expected an indeterminate rejection, got a resolved placement");
      },
      (err: unknown) => err,
    );
  }

  it("RETAINS the key on a TimeoutError — the retry gets the same answer, IB is not hit twice", async () => {
    const cause = timeoutError();
    const placement = vi
      .fn()
      .mockRejectedValueOnce(cause)
      .mockResolvedValueOnce("SECOND-ORDER-AT-IB");

    const first = await rejection(runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement));
    expect(first).toBeInstanceOf(IndeterminatePlacementError);
    expect((first as IndeterminatePlacementError).deduplicated).toBe(false);
    expect((first as IndeterminatePlacementError).cause).toBe(cause);

    const retry = await rejection(runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement));
    expect(retry).toBeInstanceOf(IndeterminatePlacementError);
    expect((retry as IndeterminatePlacementError).deduplicated).toBe(true);
    expect(placement).toHaveBeenCalledTimes(1); // the order was NOT placed again
  });

  it("treats an AbortError the same as a TimeoutError", async () => {
    const placement = vi
      .fn()
      .mockRejectedValueOnce(new DOMException("The operation was aborted", "AbortError"))
      .mockResolvedValueOnce("ok");

    await rejection(runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement));
    const retry = await rejection(runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement));

    expect(retry).toBeInstanceOf(IndeterminatePlacementError);
    expect(placement).toHaveBeenCalledTimes(1);
  });

  it("gives a CONCURRENT duplicate the same indeterminate answer", async () => {
    let failPlacement: (err: unknown) => void = () => {};
    const placement = vi.fn(() => new Promise((_res, rej) => (failPlacement = rej)));

    const first = rejection(runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement));
    const second = rejection(runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement));
    failPlacement(timeoutError());

    expect(await first).toBeInstanceOf(IndeterminatePlacementError);
    const duplicate = await second;
    expect(duplicate).toBeInstanceOf(IndeterminatePlacementError);
    expect((duplicate as IndeterminatePlacementError).deduplicated).toBe(true);
    expect(placement).toHaveBeenCalledTimes(1);
  });

  it("holds an indeterminate key for the full retention window (TTL or the floor)", async () => {
    // Retention is max(ttlMs, INDETERMINATE_RETENTION_MS): the short content-hash
    // TTL (R-051) never shrinks the indeterminate hold below the floor.
    const retention = Math.max(CONTENT_HASH_TTL_MS, INDETERMINATE_RETENTION_MS);
    vi.useFakeTimers();
    const placement = vi.fn().mockRejectedValueOnce(timeoutError()).mockResolvedValueOnce("ok");

    await rejection(runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement));

    vi.advanceTimersByTime(retention - 1_000);
    const stillHeld = await rejection(runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement));
    expect(stillHeld).toBeInstanceOf(IndeterminatePlacementError);
    expect(placement).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(1_001);
    const afterExpiry = await runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement);
    expect(afterExpiry).toEqual({ value: "ok", deduplicated: false });
    expect(placement).toHaveBeenCalledTimes(2); // normal behaviour restored
  });

  it("honours an error the caller marks indeterminate", async () => {
    const marked = markIndeterminatePlacement(new Error("upstream aborted mid-placement"));
    const placement = vi.fn().mockRejectedValueOnce(marked).mockResolvedValueOnce("ok");

    await rejection(runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement));
    const retry = await rejection(runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement));

    expect(retry).toBeInstanceOf(IndeterminatePlacementError);
    expect(placement).toHaveBeenCalledTimes(1);
  });

  it("still clears the key for a deterministic failure whose message merely says 'timed out'", async () => {
    const upstream = Object.assign(new Error("IB gateway timed out waiting for the order"), {
      name: "RadonApiError",
    });
    const placement = vi.fn().mockRejectedValueOnce(upstream).mockResolvedValueOnce("ok");

    await expect(runIdempotentOrder("k", 10_000, placement)).rejects.toThrow(
      "IB gateway timed out",
    );
    const retry = await runIdempotentOrder("k", 10_000, placement);
    expect(retry).toEqual({ value: "ok", deduplicated: false });
    expect(placement).toHaveBeenCalledTimes(2);
  });
});
