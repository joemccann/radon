import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  runIdempotentOrder,
  IndeterminatePlacementError,
  CONTENT_HASH_TTL_MS,
  INDETERMINATE_RETENTION_MS,
  __resetOrderIdempotency,
  __restartOrderIdempotencyForTests,
} from "@/lib/orders/orderIdempotency";

const FILE = path.join(
  tmpdir(),
  `radon-order-idempotency-durability-${process.pid}.json`,
);

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

const timeoutError = () =>
  new DOMException("The operation was aborted due to timeout", "TimeoutError");

/** Resolve with the rejection reason; fail loudly if the promise fulfils. */
function rejection(promise: Promise<unknown>): Promise<unknown> {
  return promise.then(
    () => {
      throw new Error("expected a rejection, got a resolved placement");
    },
    (err: unknown) => err,
  );
}

describe("R-043 — idempotency survives a process restart", () => {
  it("dedups an identical resubmit across a simulated restart", async () => {
    const placement = vi.fn(async () => "ORDER-RESULT");
    const first = await runIdempotentOrder("k", 300_000, placement);
    expect(first).toEqual({ value: "ORDER-RESULT", deduplicated: false });

    __restartOrderIdempotencyForTests(); // Map dropped, file survives

    const resubmit = await runIdempotentOrder("k", 300_000, placement);
    expect(resubmit).toEqual({ value: "ORDER-RESULT", deduplicated: true });
    expect(placement).toHaveBeenCalledTimes(1); // NOT placed twice
  });

  it("retains an INDETERMINATE outcome across a restart — the retry is refused", async () => {
    const placement = vi
      .fn()
      .mockRejectedValueOnce(timeoutError())
      .mockResolvedValueOnce("SECOND-ORDER-AT-IB");

    await rejection(runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement));

    __restartOrderIdempotencyForTests();

    const retry = await rejection(
      runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement),
    );
    expect(retry).toBeInstanceOf(IndeterminatePlacementError);
    expect((retry as IndeterminatePlacementError).deduplicated).toBe(true);
    expect(placement).toHaveBeenCalledTimes(1);
  });

  it("still honours the durable retention window after a restart, then recovers", async () => {
    // Retention is max(ttlMs, INDETERMINATE_RETENTION_MS): the short content-hash
    // TTL (R-051) never shrinks the indeterminate hold below the floor.
    const retention = Math.max(CONTENT_HASH_TTL_MS, INDETERMINATE_RETENTION_MS);
    vi.useFakeTimers();
    const placement = vi
      .fn()
      .mockRejectedValueOnce(timeoutError())
      .mockResolvedValueOnce("ok");

    await rejection(runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement));
    __restartOrderIdempotencyForTests();

    vi.advanceTimersByTime(retention - 1_000);
    const stillHeld = await rejection(
      runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement),
    );
    expect(stillHeld).toBeInstanceOf(IndeterminatePlacementError);
    expect(placement).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(1_001);
    const afterExpiry = await runIdempotentOrder("k", CONTENT_HASH_TTL_MS, placement);
    expect(afterExpiry).toEqual({ value: "ok", deduplicated: false });
    expect(placement).toHaveBeenCalledTimes(2);
  });

  it("does NOT dedupe across a restart once the TTL has expired (pruned on load)", async () => {
    vi.useFakeTimers();
    const placement = vi.fn(async () => "ok");
    await runIdempotentOrder("k", 1_000, placement);

    vi.advanceTimersByTime(1_500);
    __restartOrderIdempotencyForTests();

    await runIdempotentOrder("k", 1_000, placement);
    expect(placement).toHaveBeenCalledTimes(2);
  });

  it("does NOT persist a deterministic failure — a retry after restart re-attempts", async () => {
    const placement = vi
      .fn()
      .mockRejectedValueOnce(new Error("IB infra blip"))
      .mockResolvedValueOnce("ok");

    await expect(runIdempotentOrder("k", 300_000, placement)).rejects.toThrow(
      "IB infra blip",
    );
    __restartOrderIdempotencyForTests();

    const retry = await runIdempotentOrder("k", 300_000, placement);
    expect(retry).toEqual({ value: "ok", deduplicated: false });
    expect(placement).toHaveBeenCalledTimes(2);
  });
});

describe("durable file hygiene", () => {
  it("writes the file synchronously when a placement settles", async () => {
    await runIdempotentOrder("k", 300_000, async () => "ok");
    expect(existsSync(FILE)).toBe(true);
    expect(readFileSync(FILE, "utf8")).toContain('"k"');
  });

  it("prunes expired entries on write — the file does not grow unboundedly", async () => {
    vi.useFakeTimers();
    await runIdempotentOrder("old", 1_000, async () => "a");
    vi.advanceTimersByTime(2_000);
    await runIdempotentOrder("fresh", 300_000, async () => "b");

    const raw = readFileSync(FILE, "utf8");
    expect(raw).toContain('"fresh"');
    expect(raw).not.toContain('"old"');
  });

  it("starts empty on a corrupt file — never crashes the order route", async () => {
    writeFileSync(FILE, "{ not json !!!");
    __restartOrderIdempotencyForTests();

    const placement = vi.fn(async () => "ok");
    const result = await runIdempotentOrder("k", 300_000, placement);
    expect(result).toEqual({ value: "ok", deduplicated: false });
    expect(placement).toHaveBeenCalledTimes(1);
  });

  it("starts empty on a missing file", async () => {
    rmSync(FILE, { force: true });
    __restartOrderIdempotencyForTests();

    const result = await runIdempotentOrder("k", 300_000, async () => "ok");
    expect(result.deduplicated).toBe(false);
  });

  it("survives a non-JSON-serializable placement value without crashing (L1 still dedups)", async () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    const placement = vi.fn(async () => circular);

    const first = await runIdempotentOrder("k", 300_000, placement);
    expect(first.deduplicated).toBe(false);
    // Same process: the in-memory Map still dedups.
    const second = await runIdempotentOrder("k", 300_000, placement);
    expect(second.deduplicated).toBe(true);
    expect(placement).toHaveBeenCalledTimes(1);
  });

  it("__resetOrderIdempotency clears the durable file too", async () => {
    await runIdempotentOrder("k", 300_000, async () => "ok");
    __resetOrderIdempotency();
    expect(existsSync(FILE)).toBe(false);

    const placement = vi.fn(async () => "ok");
    const rerun = await runIdempotentOrder("k", 300_000, placement);
    expect(rerun.deduplicated).toBe(false);
  });

  it("honours RADON_ORDER_IDEMPOTENCY_FILE, creating parent directories as needed", async () => {
    const nested = path.join(
      tmpdir(),
      `radon-order-idem-nested-${process.pid}`,
      "deep",
      "store.json",
    );
    rmSync(path.dirname(path.dirname(nested)), { recursive: true, force: true });
    process.env.RADON_ORDER_IDEMPOTENCY_FILE = nested;
    __restartOrderIdempotencyForTests();

    await runIdempotentOrder("k", 300_000, async () => "ok");
    expect(existsSync(nested)).toBe(true);

    rmSync(path.dirname(path.dirname(nested)), { recursive: true, force: true });
    mkdirSync(tmpdir(), { recursive: true });
  });
});
