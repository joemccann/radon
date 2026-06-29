import { afterEach, describe, expect, it, vi } from "vitest";
import {
  runIdempotentOrder,
  contentKey,
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
