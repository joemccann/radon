import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cachedRead, invalidateCache, __clearDbCache } from "@/lib/dbCache";

describe("cachedRead", () => {
  beforeEach(() => {
    process.env.RADON_DB_CACHE_FORCE = "1"; // exercise real caching under NODE_ENV=test
    __clearDbCache();
    vi.useFakeTimers();
    vi.setSystemTime(0);
  });
  afterEach(() => {
    vi.useRealTimers();
    delete process.env.RADON_DB_CACHE_FORCE;
  });

  it("serves the cached value while fresh (fetcher runs once)", async () => {
    const fetcher = vi.fn().mockResolvedValue("v1");
    expect(await cachedRead("k", 3000, fetcher)).toBe("v1");
    vi.setSystemTime(2999);
    expect(await cachedRead("k", 3000, fetcher)).toBe("v1");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("refetches after the TTL expires", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce("v1").mockResolvedValueOnce("v2");
    expect(await cachedRead("k", 3000, fetcher)).toBe("v1");
    vi.setSystemTime(3001);
    expect(await cachedRead("k", 3000, fetcher)).toBe("v2");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("single-flights concurrent misses into one fetch", async () => {
    let resolve!: (v: string) => void;
    const fetcher = vi.fn().mockImplementation(
      () => new Promise<string>((r) => { resolve = r; }),
    );
    const a = cachedRead("k", 3000, fetcher);
    const b = cachedRead("k", 3000, fetcher);
    resolve("shared");
    expect(await a).toBe("shared");
    expect(await b).toBe("shared");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("propagates a fetch error by default (no stale fallback)", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce("v1").mockRejectedValueOnce(new Error("turso down"));
    expect(await cachedRead("k", 1000, fetcher)).toBe("v1");
    vi.setSystemTime(2000); // past TTL
    await expect(cachedRead("k", 1000, fetcher)).rejects.toThrow("turso down");
  });

  it("serves the last good value on error when staleWhileError is set", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce("v1").mockRejectedValueOnce(new Error("turso down"));
    expect(await cachedRead("k", 1000, fetcher, { staleWhileError: true })).toBe("v1");
    vi.setSystemTime(2000);
    expect(await cachedRead("k", 1000, fetcher, { staleWhileError: true })).toBe("v1");
  });

  it("does not serve stale beyond maxStaleMs", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce("v1").mockRejectedValueOnce(new Error("turso down"));
    expect(await cachedRead("k", 1000, fetcher, { staleWhileError: true, maxStaleMs: 5000 })).toBe("v1");
    vi.setSystemTime(6000); // older than maxStaleMs since storedAt(0)
    await expect(
      cachedRead("k", 1000, fetcher, { staleWhileError: true, maxStaleMs: 5000 }),
    ).rejects.toThrow("turso down");
  });

  it("invalidateCache forces a refetch", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce("v1").mockResolvedValueOnce("v2");
    expect(await cachedRead("k", 9999, fetcher)).toBe("v1");
    invalidateCache("k");
    expect(await cachedRead("k", 9999, fetcher)).toBe("v2");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
