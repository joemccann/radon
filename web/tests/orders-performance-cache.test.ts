/**
 * @vitest-environment node
 *
 * Regression contracts for the short in-process orders cache. The HTTP route
 * remains force-dynamic/no-store; this cache only coalesces direct-cloud reads.
 */
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { __clearDbCache } from "@/lib/dbCache";
import {
  ORDERS_SNAPSHOT_CACHE_TTL_MS,
  invalidateOrdersSnapshotCache,
  readCachedOrdersSnapshot,
} from "@/lib/orders/ordersReadCache";
import {
  invalidatePortfolioReadCaches,
  readCachedPortfolioContractOpenDates,
  readCachedPortfolioSnapshot,
  readCachedPortfolioTradeLogDates,
} from "@/lib/portfolio/portfolioReadCache";

const REPO_ROOT = join(__dirname, "..");

beforeEach(() => {
  process.env.RADON_DB_CACHE_FORCE = "1";
  __clearDbCache();
  vi.useFakeTimers();
  vi.setSystemTime(0);
});

afterEach(() => {
  vi.useRealTimers();
  delete process.env.RADON_DB_CACHE_FORCE;
});

describe("orders snapshot read cache", () => {
  it("single-flights concurrent reads and retains the result for two seconds", async () => {
    expect(ORDERS_SNAPSHOT_CACHE_TTL_MS).toBe(2_000);
    let resolve!: (value: string) => void;
    const fetcher = vi.fn(() => new Promise<string>((done) => { resolve = done; }));

    const first = readCachedOrdersSnapshot(fetcher);
    const concurrent = readCachedOrdersSnapshot(fetcher);
    resolve("orders-v1");

    await expect(first).resolves.toBe("orders-v1");
    await expect(concurrent).resolves.toBe("orders-v1");
    await expect(readCachedOrdersSnapshot(fetcher)).resolves.toBe("orders-v1");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("refetches after the TTL and immediately after explicit invalidation", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce("orders-v1")
      .mockResolvedValueOnce("orders-v2")
      .mockResolvedValueOnce("orders-v3");

    await expect(readCachedOrdersSnapshot(fetcher)).resolves.toBe("orders-v1");
    vi.setSystemTime(ORDERS_SNAPSHOT_CACHE_TTL_MS + 1);
    await expect(readCachedOrdersSnapshot(fetcher)).resolves.toBe("orders-v2");
    invalidateOrdersSnapshotCache();
    await expect(readCachedOrdersSnapshot(fetcher)).resolves.toBe("orders-v3");
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
});

const MUTATING_ORDER_ROUTES = [
  "app/api/orders/route.ts",
  "app/api/orders/place/route.ts",
  "app/api/orders/cancel/route.ts",
  "app/api/orders/modify/route.ts",
];

describe("order mutation cache invalidation contract", () => {
  it.each(MUTATING_ORDER_ROUTES)("%s explicitly invalidates the orders snapshot cache", async (path) => {
    const source = await readFile(join(REPO_ROOT, path), "utf8");
    expect(source).toContain("invalidateOrdersSnapshotCache");
    expect(source).toMatch(/invalidateOrdersSnapshotCache\s*\(\s*\)/);
    expect(source).toMatch(
      /invalidateOrdersSnapshotCache\s*\(\s*\)[\s\S]*?readOrdersSnapshot(?:FromDb|BestEffort)\s*\(\s*\)/,
    );
  });
});

describe("portfolio read cache policy", () => {
  it("uses the shared 15-second snapshot and 60-second entry-date bounds", async () => {
    const source = await readFile(join(REPO_ROOT, "lib/portfolio/portfolioReadCache.ts"), "utf8");
    expect(source).toMatch(/PORTFOLIO_SNAPSHOT_CACHE_TTL_MS\s*=\s*15_000/);
    expect(source).toMatch(/PORTFOLIO_ENTRY_DATES_CACHE_TTL_MS\s*=\s*60_000/);
  });

  it("invalidates all portfolio read caches on the successful live-sync path", async () => {
    const source = await readFile(join(REPO_ROOT, "app/api/portfolio/route.ts"), "utf8");
    expect(source).toMatch(
      /await\s+radonFetch\(\s*["']\/portfolio\/sync[\s\S]*?invalidatePortfolioReadCaches\s*\(\s*\)/,
    );
  });

  it("invalidates snapshot and both entry-date caches after a successful sync", async () => {
    const snapshot = vi.fn()
      .mockResolvedValueOnce("snapshot-v1")
      .mockResolvedValueOnce("snapshot-v2");
    const tradeDates = vi.fn()
      .mockResolvedValueOnce("trade-v1")
      .mockResolvedValueOnce("trade-v2");
    const contractDates = vi.fn()
      .mockResolvedValueOnce("contract-v1")
      .mockResolvedValueOnce("contract-v2");

    await readCachedPortfolioSnapshot(snapshot);
    await readCachedPortfolioTradeLogDates(tradeDates);
    await readCachedPortfolioContractOpenDates(contractDates);
    invalidatePortfolioReadCaches();

    await expect(readCachedPortfolioSnapshot(snapshot)).resolves.toMatchObject({ value: "snapshot-v2" });
    await expect(readCachedPortfolioTradeLogDates(tradeDates)).resolves.toBe("trade-v2");
    await expect(readCachedPortfolioContractOpenDates(contractDates)).resolves.toBe("contract-v2");
    expect(snapshot).toHaveBeenCalledTimes(2);
    expect(tradeDates).toHaveBeenCalledTimes(2);
    expect(contractDates).toHaveBeenCalledTimes(2);
  });
});
