/**
 * @vitest-environment node
 *
 * Demo data must be complete, current, and independent of operator-only
 * files/services. These tests pin both the fixture math and each route seam.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildDemoCashFlows } from "@/lib/demo/fixtures/cashFlows";
import { buildDemoFlowReport } from "@/lib/demo/fixtures/flowAnalysis";
import { buildDemoOrders } from "@/lib/demo/fixtures/orders";
import { buildDemoPerformance } from "@/lib/demo/fixtures/performance";
import { buildDemoThetaHarvester } from "@/lib/demo/fixtures/thetaHarvester";
import { filterExecutedToEtToday } from "@/lib/orders/executedToday";
import { buildPerformanceView } from "@/lib/performanceData";
import { businessDateKeys, marketDateKey } from "@/lib/demo/fixtures/time";

const mocks = vi.hoisted(() => ({
  getDb: vi.fn(),
  radonFetch: vi.fn(),
  readFile: vi.fn(),
  readOrders: vi.fn(),
  requireRouteAccess: vi.fn(),
  statSync: vi.fn(),
}));

vi.mock("@/lib/routeAccess", () => ({
  requireRouteAccess: mocks.requireRouteAccess,
}));

vi.mock("@/lib/radonApi", () => ({
  radonFetch: mocks.radonFetch,
  RadonApiError: class RadonApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock("@/lib/orders/readOrdersFromDb", () => ({
  readOrdersSnapshotFromDb: mocks.readOrders,
}));

vi.mock("@/lib/db", () => ({
  getDb: mocks.getDb,
  resetDb: vi.fn(),
  syncDb: vi.fn(),
}));

vi.mock("fs/promises", () => ({
  readFile: mocks.readFile,
}));

vi.mock("fs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs")>();
  return { ...actual, statSync: mocks.statSync };
});

const NOW = new Date("2026-09-04T18:15:00.000Z");

const OPEN_ORDER = {
  orderId: 41,
  permId: 8041,
  symbol: "SPY",
  contract: {
    conId: 756733,
    symbol: "SPY",
    secType: "STK",
    strike: null,
    right: null,
    expiry: null,
  },
  action: "BUY",
  orderType: "LMT",
  totalQuantity: 5,
  limitPrice: 630,
  auxPrice: null,
  status: "Submitted",
  filled: 0,
  remaining: 5,
  avgFillPrice: null,
  tif: "DAY",
};

function expectNoStore(response: Response): void {
  expect(response.headers.get("Cache-Control")?.toLowerCase()).toContain("no-store");
}

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  vi.clearAllMocks();
  mocks.requireRouteAccess.mockResolvedValue({
    ok: true,
    principal: { userId: "demo-user", kind: "demo" },
  });
  mocks.getDb.mockReturnValue({
    execute: vi.fn(() => {
      throw new Error("demo route reached Turso");
    }),
  });
  mocks.readFile.mockRejectedValue(new Error("demo route reached disk"));
  mocks.statSync.mockImplementation(() => {
    throw new Error("demo route reached disk metadata");
  });
  mocks.radonFetch.mockRejectedValue(new Error("demo route reached upstream"));
  mocks.readOrders.mockResolvedValue({
    last_sync: "",
    open_orders: [OPEN_ORDER],
    executed_orders: [],
    open_count: 1,
    executed_count: 0,
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("production-owned demo fixtures", () => {
  it("builds a complete, reproducible performance series from an injected clock", () => {
    const first = buildDemoPerformance(NOW);
    const second = buildDemoPerformance(NOW);

    expect(first).toEqual(second);
    expect(first.status).toBe("ok");
    expect(first.series.length).toBeGreaterThanOrEqual(250);
    expect(first.counts?.n_nav_observations).toBe(first.series.length);
    expect(first.nav_as_of).toBe(first.series.at(-1)?.date);
    expect(first.twr?.cum_return).toBeTypeOf("number");
    expect(first.risk?.sharpe_ratio.value).toBeTypeOf("number");
    expect(first.equity?.ending).toBe(first.series.at(-1)?.nav);
    expect(first.benchmark && typeof first.benchmark !== "string" ? first.benchmark.r_squared : null)
      .toBeCloseTo((first.benchmark && typeof first.benchmark !== "string" ? first.benchmark.correlation : 0) ** 2, 5);
    expect(buildPerformanceView(first)).toMatchObject({
      status: "ok",
      isInsufficient: false,
      isStale: false,
      annualized: { unavailable_reason: null },
    });
  });

  it("uses ET dates and skips weekends plus full-closure market holidays", () => {
    const afterUtcMidnight = new Date("2026-09-05T00:05:00.000Z");
    const afterLaborDay = new Date("2026-09-08T18:00:00.000Z");

    expect(marketDateKey(afterUtcMidnight)).toBe("2026-09-04");
    expect(businessDateKeys(2, afterLaborDay)).toEqual(["2026-09-04", "2026-09-08"]);
    const orders = buildDemoOrders({
      last_sync: "",
      open_orders: [],
      executed_orders: [],
      open_count: 0,
      executed_count: 0,
    }, afterUtcMidnight);
    expect(orders.executed_orders[0].execId).toContain("20260904");
    expect(filterExecutedToEtToday(orders.executed_orders, afterUtcMidnight)).toHaveLength(3);
  });

  it("filters cash flows by lookback and comma-separated transaction type", () => {
    const all = buildDemoCashFlows({ now: NOW, days: 90, types: "" });
    const filtered = buildDemoCashFlows({ now: NOW, days: 30, types: "Withdrawal,Dividend" });

    expect(all.rows.length).toBeGreaterThan(filtered.rows.length);
    expect(filtered.rows.every((row) => row.type === "Withdrawal" || row.type === "Dividend")).toBe(true);
    expect(filtered.summary?.net).toBe(
      filtered.rows.reduce((total, row) => total + row.amount, 0),
    );
  });

  it("keeps demo executions on the injected clock while preserving open orders", () => {
    const orders = buildDemoOrders({
      last_sync: "",
      open_orders: [OPEN_ORDER],
      executed_orders: [],
      open_count: 1,
      executed_count: 0,
    }, NOW);

    expect(orders.open_orders).toEqual([OPEN_ORDER]);
    expect(orders.executed_orders.length).toBeGreaterThan(0);
    expect(orders.executed_orders.every((order) => order.time === NOW.toISOString())).toBe(true);
    expect(filterExecutedToEtToday(orders.executed_orders, NOW)).toHaveLength(orders.executed_orders.length);
    expect(orders.executed_count).toBe(orders.executed_orders.length);
  });

  it("builds coherent theta candidates and honors ticker scan filters", () => {
    const data = buildDemoThetaHarvester({
      now: NOW,
      ticker: "SNDK",
      minDte: 20,
      maxDte: 35,
      minCredit: 1,
    });

    expect(data.requested_tickers).toEqual(["SNDK"]);
    expect(data.results).toHaveLength(1);
    expect(data.results[0].ticker).toBe("SNDK");
    expect(data.results[0].structure.dte).toBeGreaterThanOrEqual(20);
    expect(data.results[0].structure.dte).toBeLessThanOrEqual(35);
    expect(data.results[0].structure.expiry).toMatch(/^\d{8}$/);
    const expiry = data.results[0].structure.expiry;
    expect(new Date(`${expiry.slice(0, 4)}-${expiry.slice(4, 6)}-${expiry.slice(6, 8)}T12:00:00.000Z`).getUTCDay()).toBe(5);
    expect(data.theta_harvest_count).toBe(1);
  });

  it("builds a fresh, full report for any valid ticker without randomness", () => {
    const first = buildDemoFlowReport("SNDK", NOW);
    const second = buildDemoFlowReport("SNDK", NOW);

    expect(first).toEqual(second);
    expect(first.ticker).toBe("SNDK");
    expect(first.fetched_at).toBe(NOW.toISOString());
    expect(first.verdict).toBeTruthy();
    expect(first.dark_pool?.daily?.length).toBeGreaterThan(5);
    expect(first.options_flow?.total_alerts).toBeGreaterThan(0);
    expect(first.cache_meta).toMatchObject({ age_seconds: 0, is_stale: false });
  });
});

describe("demo route seams", () => {
  it("serves performance before Turso or disk access", async () => {
    const { GET } = await import("@/app/api/performance/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expectNoStore(response);
    expect(body.status).toBe("ok");
    expect(body.series.length).toBeGreaterThan(0);
    expect(mocks.getDb().execute).not.toHaveBeenCalled();
    expect(mocks.readFile).not.toHaveBeenCalled();
  });

  it("merges stable current fills with the demo DB's open orders", async () => {
    const { GET } = await import("@/app/api/orders/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expectNoStore(response);
    expect(body.open_orders).toEqual([OPEN_ORDER]);
    expect(body.executed_orders.length).toBeGreaterThan(0);
    expect(mocks.readOrders).toHaveBeenCalledTimes(1);
  });

  it("serves filtered cash flows without FastAPI", async () => {
    const { GET } = await import("@/app/api/cash-flows/route");
    const response = await GET(
      new Request("http://localhost/api/cash-flows?days=30&types=Withdrawal") as never,
    );
    const body = await response.json();

    expect(response.status).toBe(200);
    expectNoStore(response);
    expect(body.rows.length).toBeGreaterThan(0);
    expect(body.rows.every((row: { type: string }) => row.type === "Withdrawal")).toBe(true);
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("serves theta GET and POST scans without cache or upstream access", async () => {
    const { GET } = await import("@/app/api/scanner/theta/route");
    const getResponse = await GET();
    const getBody = await getResponse.json();
    expect(getBody.results.length).toBeGreaterThan(0);

    const { POST } = await import("@/app/api/scanner/theta/scan/route");
    const postResponse = await POST(new Request("http://localhost/api/scanner/theta/scan", {
      method: "POST",
      body: JSON.stringify({ ticker: "SNDK", min_dte: 20, max_dte: 35, min_credit: 1 }),
    }));
    const postBody = await postResponse.json();

    expect(postResponse.status).toBe(200);
    expectNoStore(postResponse);
    expect(postBody.scan_succeeded).toBe(true);
    expect(postBody.results[0].ticker).toBe("SNDK");
    expect(mocks.readFile).not.toHaveBeenCalled();
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("keeps theta ticker validation ahead of the demo fixture", async () => {
    const { POST } = await import("@/app/api/scanner/theta/scan/route");
    const response = await POST(new Request("http://localhost/api/scanner/theta/scan", {
      method: "POST",
      body: JSON.stringify({ ticker: "BAD1" }),
    }));

    expect(response.status).toBe(400);
    expectNoStore(response);
  });

  it("serves fresh GET and POST flow reports for arbitrary valid tickers", async () => {
    const route = await import("@/app/api/flow-analysis/[ticker]/route");
    const context = { params: Promise.resolve({ ticker: "sndk" }) };
    const request = new Request("http://localhost/api/flow-analysis/sndk");
    const getResponse = await route.GET(request, context);
    const postResponse = await route.POST(request, context);
    const getBody = await getResponse.json();
    const postBody = await postResponse.json();

    expect(getBody).toMatchObject({ ticker: "SNDK", fetched_at: NOW.toISOString() });
    expect(postBody).toMatchObject({ ticker: "SNDK", fetched_at: NOW.toISOString() });
    expectNoStore(getResponse);
    expectNoStore(postResponse);
    expect(mocks.readFile).not.toHaveBeenCalled();
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("keeps flow ticker validation ahead of the demo fixture", async () => {
    const { GET } = await import("@/app/api/flow-analysis/[ticker]/route");
    const response = await GET(
      new Request("http://localhost/api/flow-analysis/BAD1"),
      { params: Promise.resolve({ ticker: "BAD1" }) },
    );

    expect(response.status).toBe(400);
    expectNoStore(response);
  });
});
