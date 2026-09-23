import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { RateLimiter } from "../../scripts/lib/rate-limiter.js";

describe("RateLimiter", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("resolves a single submission", async () => {
    const limiter = new RateLimiter(10);
    const promise = limiter.submit(() => 42);
    vi.advanceTimersByTime(1000);
    const result = await promise;
    expect(result).toBe(42);
    limiter.clear();
  });

  it("processes up to maxPerSecond items per tick", async () => {
    const limiter = new RateLimiter(2);
    const results: number[] = [];

    const p1 = limiter.submit(() => 1).then((v) => results.push(v));
    const p2 = limiter.submit(() => 2).then((v) => results.push(v));
    const p3 = limiter.submit(() => 3).then((v) => results.push(v));

    // First tick processes 2 items (maxPerSecond = 2)
    vi.advanceTimersByTime(1000);
    await Promise.resolve(); // flush microtasks
    await Promise.resolve();
    expect(results).toEqual([1, 2]);

    // Second tick processes the remaining 1 item
    vi.advanceTimersByTime(1000);
    await Promise.resolve();
    await Promise.resolve();
    expect(results).toEqual([1, 2, 3]);

    limiter.clear();
  });

  it("preserves FIFO ordering", async () => {
    const limiter = new RateLimiter(10);
    const order: string[] = [];

    const p1 = limiter.submit(() => "first").then((v) => order.push(v));
    const p2 = limiter.submit(() => "second").then((v) => order.push(v));
    const p3 = limiter.submit(() => "third").then((v) => order.push(v));

    vi.advanceTimersByTime(1000);
    await Promise.resolve();
    await Promise.resolve();
    expect(order).toEqual(["first", "second", "third"]);
    limiter.clear();
  });

  it("rejects when fn throws", async () => {
    const limiter = new RateLimiter(10);
    const promise = limiter.submit(() => {
      throw new Error("boom");
    });
    vi.advanceTimersByTime(1000);
    await expect(promise).rejects.toThrow("boom");
    limiter.clear();
  });

  it("clear() empties the queue", () => {
    const limiter = new RateLimiter(10);
    limiter.submit(() => 1);
    limiter.submit(() => 2);
    limiter.submit(() => 3);
    expect(limiter.pending).toBe(3);
    limiter.clear();
    expect(limiter.pending).toBe(0);
  });

  it("pending tracks queue length", () => {
    const limiter = new RateLimiter(1);
    expect(limiter.pending).toBe(0);
    limiter.submit(() => "a");
    limiter.submit(() => "b");
    expect(limiter.pending).toBe(2);
    // Process first batch
    vi.advanceTimersByTime(1000);
    expect(limiter.pending).toBe(1);
    vi.advanceTimersByTime(1000);
    expect(limiter.pending).toBe(0);
    limiter.clear();
  });

  it("stops timer when queue drains", async () => {
    const limiter = new RateLimiter(10);
    const promise = limiter.submit(() => "done");
    vi.advanceTimersByTime(1000);
    await promise;
    // After drain, one more tick should clear timer. No more intervals running
    vi.advanceTimersByTime(1000);
    expect(limiter.pending).toBe(0);
    limiter.clear();
  });
  it("rejects submissions beyond maxQueue instead of growing without bound", async () => {
    const limiter = new RateLimiter(1, { maxQueue: 2 });
    const a = limiter.submit(() => "a");
    const b = limiter.submit(() => "b");
    await expect(limiter.submit(() => "c")).rejects.toThrow(/queue full/i);
    expect(limiter.pending).toBe(2);
    vi.advanceTimersByTime(2000);
    await expect(Promise.all([a, b])).resolves.toEqual(["a", "b"]);
    limiter.clear();
  });
});
