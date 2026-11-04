import { readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  runIdempotentOrder,
  IndeterminatePlacementError,
  INDETERMINATE_RETENTION_MS,
  IN_FLIGHT_MAX_MS,
  __resetOrderIdempotency,
  __restartOrderIdempotencyForTests,
} from "@/lib/orders/orderIdempotency";

/**
 * Audit 2026-11-03 §1 #12. An in-flight placement was never persisted, so a
 * restart mid-placement dropped the guard and the client retry placed a second
 * order. MAX_ENTRIES eviction dropped the oldest key regardless of state, so a
 * burst of settled keys could evict an in-flight or indeterminate hold.
 */

const FILE = path.join(tmpdir(), `radon-order-idempotency-inflight-${process.pid}.json`);

beforeEach(() => {
  process.env.RADON_ORDER_IDEMPOTENCY_FILE = FILE;
  __resetOrderIdempotency();
});

afterEach(() => {
  __resetOrderIdempotency();
  delete process.env.RADON_ORDER_IDEMPOTENCY_FILE;
  rmSync(FILE, { force: true });
  vi.useRealTimers();
});

function rejection(promise: Promise<unknown>): Promise<unknown> {
  return promise.then(
    () => {
      throw new Error("expected a rejection, got a resolved placement");
    },
    (err: unknown) => err,
  );
}

const timeoutError = () =>
  new DOMException("The operation was aborted due to timeout", "TimeoutError");

describe("in-flight placements survive a restart", () => {
  it("persists the in-flight key before placement resolves", async () => {
    const pending = runIdempotentOrder("k", 15_000, () => new Promise(() => {}));
    void pending;
    expect(readFileSync(FILE, "utf8")).toContain('"k"');
  });

  it("a retry after a mid-placement restart is held as indeterminate, not re-placed", async () => {
    void runIdempotentOrder("k", 15_000, () => new Promise(() => {}));
    __restartOrderIdempotencyForTests();

    const second = vi.fn(async () => "SECOND-ORDER");
    const retry = await rejection(runIdempotentOrder("k", 15_000, second));

    expect(retry).toBeInstanceOf(IndeterminatePlacementError);
    expect((retry as IndeterminatePlacementError).deduplicated).toBe(true);
    expect(second).not.toHaveBeenCalled();
  });

  it("the restart tombstone expires after the placement budget plus retention", async () => {
    vi.useFakeTimers();
    void runIdempotentOrder("k", 15_000, () => new Promise(() => {}));
    __restartOrderIdempotencyForTests();

    vi.advanceTimersByTime(IN_FLIGHT_MAX_MS + INDETERMINATE_RETENTION_MS + 1);
    const second = vi.fn(async () => "ok");
    const retry = await runIdempotentOrder("k", 15_000, second);

    expect(retry).toEqual({ value: "ok", deduplicated: false });
  });

  it("a deterministic failure removes the in-flight record from disk", async () => {
    await expect(
      runIdempotentOrder("k", 300_000, async () => {
        throw new Error("rejected");
      }),
    ).rejects.toThrow("rejected");
    __restartOrderIdempotencyForTests();

    const retry = await runIdempotentOrder("k", 300_000, async () => "ok");
    expect(retry).toEqual({ value: "ok", deduplicated: false });
  });
});

describe("MAX_ENTRIES eviction never drops a live guard", () => {
  it("keeps an in-flight key while settled keys overflow the cap", async () => {
    let release: (v: string) => void = () => {};
    const first = runIdempotentOrder("guard", 300_000, () => new Promise<string>((r) => { release = r; }));
    for (let i = 0; i < 1_005; i += 1) {
      await runIdempotentOrder(`fill-${i}`, 300_000, async () => i);
    }

    const duplicate = vi.fn(async () => "DUPLICATE");
    const dup = runIdempotentOrder("guard", 300_000, duplicate);
    release("FIRST");

    expect(await first).toEqual({ value: "FIRST", deduplicated: false });
    expect(await dup).toEqual({ value: "FIRST", deduplicated: true });
    expect(duplicate).not.toHaveBeenCalled();
  });

  it("keeps an indeterminate key while settled keys overflow the cap", async () => {
    await rejection(runIdempotentOrder("held", 15_000, async () => { throw timeoutError(); }));
    for (let i = 0; i < 1_005; i += 1) {
      await runIdempotentOrder(`fill-${i}`, 300_000, async () => i);
    }

    const duplicate = vi.fn(async () => "DUPLICATE");
    const retry = await rejection(runIdempotentOrder("held", 15_000, duplicate));
    expect(retry).toBeInstanceOf(IndeterminatePlacementError);
    expect(duplicate).not.toHaveBeenCalled();
  });
});
