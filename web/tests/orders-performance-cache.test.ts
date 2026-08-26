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
  PORTFOLIO_ENTRY_DATES_CACHE_TTL_MS,
  PORTFOLIO_SNAPSHOT_CACHE_TTL_MS,
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

// Structural floor only: presence + ordering of the invalidate/read pair.
// The reachability contract this cannot express — that the SECOND, post-refresh
// invalidate exists, so a GET racing `/orders/refresh` cannot serve the mutating
// route its own stale snapshot — is driven behaviourally in
// web/tests/orders-place-cache-race.test.ts (T-169).
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
  // T-168: the declared constant AND the wiring. Asserting the source text of
  // the declaration left `cachedReadResult(key, 0, ...)` green, so every read
  // could miss the cache while the regex still matched. Literal 14_999/15_001
  // boundaries (never the constant itself) keep this from going self-referential.
  it("serves one snapshot fetch for the whole 15-second window, then refetches", async () => {
    expect(PORTFOLIO_SNAPSHOT_CACHE_TTL_MS).toBe(15_000);
    const fetcher = vi.fn()
      .mockResolvedValueOnce("snapshot-v1")
      .mockResolvedValueOnce("snapshot-v2");

    await expect(readCachedPortfolioSnapshot(fetcher)).resolves.toMatchObject({ value: "snapshot-v1" });
    await expect(readCachedPortfolioSnapshot(fetcher)).resolves.toMatchObject({ value: "snapshot-v1" });
    vi.setSystemTime(14_999);
    await expect(readCachedPortfolioSnapshot(fetcher)).resolves.toMatchObject({ value: "snapshot-v1" });
    expect(fetcher).toHaveBeenCalledTimes(1);

    vi.setSystemTime(15_001);
    await expect(readCachedPortfolioSnapshot(fetcher)).resolves.toMatchObject({ value: "snapshot-v2" });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("serves one entry-date fetch for the whole 60-second window, then refetches", async () => {
    expect(PORTFOLIO_ENTRY_DATES_CACHE_TTL_MS).toBe(60_000);
    const tradeDates = vi.fn()
      .mockResolvedValueOnce("trade-v1")
      .mockResolvedValueOnce("trade-v2");
    const contractDates = vi.fn()
      .mockResolvedValueOnce("contract-v1")
      .mockResolvedValueOnce("contract-v2");

    await expect(readCachedPortfolioTradeLogDates(tradeDates)).resolves.toBe("trade-v1");
    await expect(readCachedPortfolioContractOpenDates(contractDates)).resolves.toBe("contract-v1");
    vi.setSystemTime(59_999);
    await expect(readCachedPortfolioTradeLogDates(tradeDates)).resolves.toBe("trade-v1");
    await expect(readCachedPortfolioContractOpenDates(contractDates)).resolves.toBe("contract-v1");
    expect(tradeDates).toHaveBeenCalledTimes(1);
    expect(contractDates).toHaveBeenCalledTimes(1);

    vi.setSystemTime(60_001);
    await expect(readCachedPortfolioTradeLogDates(tradeDates)).resolves.toBe("trade-v2");
    await expect(readCachedPortfolioContractOpenDates(contractDates)).resolves.toBe("contract-v2");
    expect(tradeDates).toHaveBeenCalledTimes(2);
    expect(contractDates).toHaveBeenCalledTimes(2);
  });

  // T-169 sibling: the old bridging regex over the route source stayed green if
  // the invalidation was wrapped in an env guard. Drive the real handler and
  // prove a caller re-reading INSIDE the TTL sees post-sync values.
  it("invalidates all portfolio read caches on the successful live-sync path", async () => {
    const radonFetch = vi.fn().mockResolvedValue({ positions: [], bankroll: 1 });
    vi.doMock("@/lib/radonApi", () => ({
      radonFetch,
      radonFetchText: vi.fn(),
      radonErrorDetailText: (detail: unknown) => String(detail),
      coerceRadonErrorDetail: (detail: unknown) => detail,
      RadonApiError: class RadonApiError extends Error {},
    }));
    const { POST } = await import("../app/api/portfolio/route");

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

    const response = await POST();
    expect(response.status).toBe(200);
    expect(radonFetch).toHaveBeenCalledWith("/portfolio/sync", expect.objectContaining({ method: "POST" }));

    // 1s in: every TTL is still live, so only a real invalidation yields v2.
    vi.setSystemTime(1_000);
    await expect(readCachedPortfolioSnapshot(snapshot)).resolves.toMatchObject({ value: "snapshot-v2" });
    await expect(readCachedPortfolioTradeLogDates(tradeDates)).resolves.toBe("trade-v2");
    await expect(readCachedPortfolioContractOpenDates(contractDates)).resolves.toBe("contract-v2");
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
