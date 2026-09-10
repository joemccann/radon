import { describe, it, expect, vi, beforeEach } from "vitest";
import { mostRecentSessionDate } from "../lib/marketSession";

/**
 * API route tests.
 *
 * Part 1: Input validation — verify routes return proper 400 errors for
 * missing/invalid parameters BEFORE reaching external services.
 *
 * Part 2: Mocked success paths — verify routes return correct data shapes
 * when underlying dependencies succeed or fail gracefully.
 *
 * External service calls are mocked to prevent real network/process activity.
 */

// ---------------------------------------------------------------------------
// Mocks — must be declared before imports (vi.mock is hoisted)
// ---------------------------------------------------------------------------

// Mock @tools/runner so routes that import runScript don't spawn real processes
vi.mock("@tools/runner", () => ({
  runScript: vi.fn().mockResolvedValue({ ok: false, stderr: "mocked" }),
  resolveProjectRoot: vi.fn().mockReturnValue("/mock/root"),
}));

// Mock @tools/wrappers/ib-orders for orders routes (legacy — no longer used by routes)
const mockIbOrders = vi.fn().mockResolvedValue({ ok: false, stderr: "mocked" });
vi.mock("@tools/wrappers/ib-orders", () => ({
  ibOrders: mockIbOrders,
}));

// Mock @tools/wrappers/ib-sync for portfolio route (legacy — no longer used by routes)
const mockIbSync = vi.fn().mockResolvedValue({ ok: false, stderr: "mocked" });
vi.mock("@tools/wrappers/ib-sync", () => ({
  ibSync: mockIbSync,
}));

// Mock @/lib/radonApi — the NEW dependency for migrated routes
const mockRadonFetch = vi.fn().mockRejectedValue(new Error("mocked"));
vi.mock("@/lib/radonApi", () => ({
  radonFetch: mockRadonFetch,
  RadonApiError: class extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(`Radon API ${status}: ${detail}`);
      this.name = "RadonApiError";
      this.status = status;
      this.detail = detail;
    }
  },
}));

// Mock @tools/data-reader for routes that still read cached files
const mockReadDataFile = vi.fn().mockResolvedValue({ ok: false, error: "not found" });
vi.mock("@tools/data-reader", () => ({
  readDataFile: mockReadDataFile,
}));

const emptyOrdersSnapshot = {
  last_sync: "",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const mockReadOrdersSnapshotFromDb = vi.fn().mockResolvedValue(emptyOrdersSnapshot);
vi.mock("@/lib/orders/readOrdersFromDb", () => ({
  readOrdersSnapshotFromDb: mockReadOrdersSnapshotFromDb,
}));

const mockExecute = vi.fn().mockResolvedValue({ rows: [] });
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => ({ execute: mockExecute }) }));

// Mock @tools/schemas/ib-orders (TypeBox schema import)
vi.mock("@tools/schemas/ib-orders", () => ({
  OrdersData: {},
}));

// Mock @tools/schemas/ib-sync (TypeBox schema import)
vi.mock("@tools/schemas/ib-sync", () => ({
  PortfolioData: {},
}));

// Mock @/lib/syncMutex — return a factory that wraps the provided fn
vi.mock("@/lib/syncMutex", () => ({
  createSyncMutex: (fn: () => Promise<unknown>) => fn,
}));

// Mock fs/promises for blotter + journal + portfolio routes
const mockReadFile = vi.fn();
const mockReaddir = vi.fn().mockResolvedValue([]);
const mockMkdir = vi.fn().mockResolvedValue(undefined);
const mockWriteFile = vi.fn().mockResolvedValue(undefined);
// Default stat: mtime 5 s ago (fresh) so portfolio GET doesn't trigger background spawn
const mockStat = vi.fn().mockResolvedValue({ mtimeMs: Date.now() - 5_000 });
// T-478: the module-load-time default freezes and leaks per-test stubs —
// re-stamp a fresh window-relative mtime before every test.
beforeEach(() => {
  mockStat.mockReset();
  mockStat.mockResolvedValue({ mtimeMs: Date.now() - 5_000 });
});
vi.mock("fs/promises", () => ({
  readFile: mockReadFile,
  readdir: mockReaddir,
  mkdir: mockMkdir,
  writeFile: mockWriteFile,
  stat: mockStat,
}));

// Mock child_process — spawn returns a minimal stub so portfolio route doesn't crash
// when the background sync is triggered (e.g. if stat is mocked stale in a specific test).
const spawnStub = {
  stdout: { on: vi.fn() },
  stderr: { on: vi.fn() },
  on: vi.fn(),
  unref: vi.fn(),
};
vi.mock("child_process", () => ({
  spawn: vi.fn().mockReturnValue(spawnStub),
}));

// Mock ws — WebSocket used by /api/previous-close for IB snapshots
// Default: emit error so IB source fails gracefully in validation tests
vi.mock("ws", () => {
  class MockWebSocket {
    handlers: Record<string, Function> = {};
    constructor() {
      setTimeout(() => { this.handlers.error?.(); }, 0);
    }
    on(event: string, fn: Function) { this.handlers[event] = fn; return this; }
    send() { /* no-op */ }
    close() { this.handlers.close?.(); }
  }
  return { WebSocket: MockWebSocket };
});

// Mock global fetch for routes that call external APIs directly
const mockFetch = vi.fn().mockResolvedValue({
  ok: false,
  json: async () => ({}),
});
vi.stubGlobal("fetch", mockFetch);

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function makeRequest(url: string, init?: RequestInit): Request {
  return new Request(url, init);
}

function mockPortfolioDb(portfolio: Record<string, unknown> | null, journalRows: Record<string, string>[] = []) {
  mockExecute.mockImplementation(async ({ sql }: { sql: string }) => {
    if (/FROM\s+portfolio_snapshots/i.test(sql)) {
      return {
        rows: portfolio
          ? [{ taken_at: portfolio.last_sync, payload: JSON.stringify(portfolio) }]
          : [],
      };
    }
    if (/FROM\s+journal/i.test(sql)) {
      return { rows: journalRows };
    }
    return { rows: [] };
  });
}

function mockJournalDb(trades: Record<string, unknown>[] = []) {
  mockExecute.mockImplementation(async ({ sql }: { sql: string }) => {
    if (/FROM\s+journal/i.test(sql)) {
      return { rows: trades.map((trade) => ({ payload: JSON.stringify(trade) })) };
    }
    if (/FROM\s+reconciliation_log/i.test(sql)) {
      return { rows: [] };
    }
    if (/INSERT\s+INTO\s+journal/i.test(sql)) {
      return { rows: [] };
    }
    return { rows: [] };
  });
}

function mockJournalImportDb(reconciliation: Record<string, unknown>) {
  const trades: Record<string, unknown>[] = [];
  mockExecute.mockImplementation(async ({ sql, args }: { sql: string; args?: unknown[] }) => {
    if (/FROM\s+reconciliation_log/i.test(sql)) {
      return { rows: [{ payload: JSON.stringify(reconciliation) }] };
    }
    if (/FROM\s+journal/i.test(sql)) {
      return { rows: trades.map((trade) => ({ payload: JSON.stringify(trade) })) };
    }
    if (/INSERT\s+INTO\s+journal/i.test(sql)) {
      if (typeof args?.[1] === "string") {
        trades.push(JSON.parse(args[1]) as Record<string, unknown>);
      }
      return { rows: [] };
    }
    return { rows: [] };
  });
}

// =============================================================================
// GET /api/ticker/ratings
// =============================================================================

describe("GET /api/ticker/ratings", () => {
  let GET: (req: Request) => Promise<Response>;

  beforeEach(async () => {
    const mod = await import("../app/api/ticker/ratings/route");
    GET = mod.GET;
  });

  it("returns 400 when ticker param is missing", async () => {
    const req = makeRequest("http://localhost/api/ticker/ratings");
    const res = await GET(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("ticker");
  });

  it("returns 400 for empty ticker param", async () => {
    const req = makeRequest("http://localhost/api/ticker/ratings?ticker=");
    const res = await GET(req);
    // Empty string is falsy, so it should be caught as missing
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("ticker");
  });
});

// =============================================================================
// GET /api/ticker/news
// =============================================================================

describe("GET /api/ticker/news", () => {
  let GET: (req: Request) => Promise<Response>;

  beforeEach(async () => {
    const mod = await import("../app/api/ticker/news/route");
    GET = mod.GET;
    mockFetch.mockReset();
    mockFetch.mockResolvedValue({
      ok: false,
      json: async () => ({}),
    });
  });

  it("returns 400 when ticker param is missing", async () => {
    const req = makeRequest("http://localhost/api/ticker/news");
    const res = await GET(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("ticker");
  });

  it("returns 400 for empty ticker param", async () => {
    const req = makeRequest("http://localhost/api/ticker/news?ticker=");
    const res = await GET(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("ticker");
  });
});

// =============================================================================
// GET /api/ticker/seasonality
// =============================================================================

describe("GET /api/ticker/seasonality", () => {
  let GET: (req: Request) => Promise<Response>;

  beforeEach(async () => {
    const mod = await import("../app/api/ticker/seasonality/route");
    GET = mod.GET;
    mockFetch.mockReset();
    mockFetch.mockResolvedValue({
      ok: false,
      json: async () => ({}),
    });
  });

  it("returns 400 when ticker param is missing", async () => {
    const req = makeRequest("http://localhost/api/ticker/seasonality");
    const res = await GET(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("ticker");
  });

  it("returns 400 for empty ticker param", async () => {
    const req = makeRequest("http://localhost/api/ticker/seasonality?ticker=");
    const res = await GET(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("ticker");
  });
});

// =============================================================================
// POST /api/orders/place
// =============================================================================

describe("POST /api/orders/place", () => {
  let POST: (req: Request) => Promise<Response>;

  beforeEach(async () => {
    const mod = await import("../app/api/orders/place/route");
    POST = mod.POST;
  });

  it("returns 400 when symbol is missing", async () => {
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "BUY", quantity: 10, limitPrice: 150 }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("symbol");
  });

  it("returns 400 when action is missing", async () => {
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: "AAPL", quantity: 10, limitPrice: 150 }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("action");
  });

  it("returns 400 when quantity is missing", async () => {
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: "AAPL", action: "BUY", limitPrice: 150 }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 when limitPrice is missing", async () => {
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: "AAPL", action: "BUY", quantity: 10 }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 for options without expiry", async () => {
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "option",
        symbol: "AAPL",
        action: "BUY",
        quantity: 1,
        limitPrice: 5.0,
        strike: 200,
        right: "C",
      }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("expiry");
  });

  it("returns 400 for options without strike", async () => {
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "option",
        symbol: "AAPL",
        action: "BUY",
        quantity: 1,
        limitPrice: 5.0,
        expiry: "20260320",
        right: "C",
      }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("strike");
  });

  it("returns 400 for options without right", async () => {
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "option",
        symbol: "AAPL",
        action: "BUY",
        quantity: 1,
        limitPrice: 5.0,
        expiry: "20260320",
        strike: 200,
      }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("right");
  });

  it("returns 400 for completely empty body", async () => {
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 for combo without legs", async () => {
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "combo",
        symbol: "PLTR",
        action: "SELL",
        quantity: 50,
        limitPrice: 8.50,
      }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("legs");
  });

  it("returns 400 for combo with only 1 leg", async () => {
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "combo",
        symbol: "PLTR",
        action: "SELL",
        quantity: 50,
        limitPrice: 8.50,
        legs: [
          { expiry: "20260327", strike: 145, right: "C", action: "SELL", ratio: 1 },
        ],
      }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("legs");
  });

  it("returns 400 for combo with empty legs array", async () => {
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "combo",
        symbol: "PLTR",
        action: "SELL",
        quantity: 50,
        limitPrice: 8.50,
        legs: [],
      }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("legs");
  });
});

// =============================================================================
// POST /api/previous-close
// =============================================================================

describe("POST /api/previous-close", () => {
  let POST: (req: Request) => Promise<Response>;

  beforeEach(async () => {
    const mod = await import("../app/api/previous-close/route");
    POST = mod.POST;
    mockFetch.mockReset();
    mockFetch.mockResolvedValue({
      ok: false,
      json: async () => ({}),
    });
  });

  it("rejects an empty symbols array", async () => {
    const req = makeRequest("http://localhost/api/previous-close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols: [] }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body).toEqual({ error: "symbols must contain 1 to 30 valid unique tickers" });
  });

  it("rejects a non-array symbols value", async () => {
    const req = makeRequest("http://localhost/api/previous-close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols: "AAPL" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body).toEqual({ error: "symbols must contain 1 to 30 valid unique tickers" });
  });

  it("rejects a missing symbols field", async () => {
    const req = makeRequest("http://localhost/api/previous-close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body).toEqual({ error: "symbols must contain 1 to 30 valid unique tickers" });
  });
});

// =============================================================================
// GET /api/portfolio — success paths
// =============================================================================

describe("GET /api/portfolio", () => {
  let GET: () => Promise<Response>;

  beforeEach(async () => {
    vi.resetModules();
    mockReadDataFile.mockReset();
    mockExecute.mockReset();
    mockExecute.mockResolvedValue({ rows: [] });
    const mod = await import("../app/api/portfolio/route");
    GET = mod.GET;
  });

  it("returns portfolio data when a Turso snapshot exists", async () => {
    const mockPortfolio = {
      bankroll: 100000,
      peak_value: 105000,
      last_sync: "2026-03-05T10:00:00",
      positions: [],
      total_deployed_pct: 25.0,
      total_deployed_dollars: 25000,
      remaining_capacity_pct: 75.0,
      position_count: 0,
      defined_risk_count: 0,
      undefined_risk_count: 0,
      avg_kelly_optimal: null,
    };
    mockPortfolioDb(mockPortfolio);

    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.bankroll).toBe(100000);
    expect(body.positions).toEqual([]);
    expect(body.position_count).toBe(0);
    expect(mockReadDataFile).not.toHaveBeenCalled();
  });

  it("returns 503 without live sync when no Turso snapshot exists", async () => {
    mockPortfolioDb(null);

    const res = await GET();
    expect(res.status).toBe(503);
    const body = await res.json();
    expect(body.error).toContain("Portfolio snapshot unavailable");
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });
});

// =============================================================================
// POST /api/portfolio — success paths
// =============================================================================

describe("POST /api/portfolio", () => {
  let POST: () => Promise<Response>;

  beforeEach(async () => {
    vi.resetModules();
    mockRadonFetch.mockReset();
    mockReadDataFile.mockReset();
    mockExecute.mockReset();
    mockExecute.mockResolvedValue({ rows: [] });
    const mod = await import("../app/api/portfolio/route");
    POST = mod.POST;
  });

  it("returns synced data on success", async () => {
    const mockPortfolio = {
      bankroll: 98000,
      peak_value: 102000,
      last_sync: "2026-03-05T14:30:00",
      positions: [],
      total_deployed_pct: 10.0,
      total_deployed_dollars: 9800,
      remaining_capacity_pct: 90.0,
      position_count: 0,
      defined_risk_count: 0,
      undefined_risk_count: 0,
      avg_kelly_optimal: null,
    };
    mockRadonFetch.mockResolvedValue(mockPortfolio);

    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.bankroll).toBe(98000);
    expect(body.last_sync).toBe("2026-03-05T14:30:00");
  });

  it("returns 502 when sync fails and no cache", async () => {
    mockRadonFetch.mockRejectedValue(new Error("connection refused"));
    mockPortfolioDb(null);

    const res = await POST();
    expect(res.status).toBe(502);
  });
});

// =============================================================================
// GET /api/orders — success paths
// =============================================================================

describe("GET /api/orders", () => {
  let GET: () => Promise<Response>;

  beforeEach(async () => {
    vi.resetModules();
    mockReadDataFile.mockReset();
    mockReadOrdersSnapshotFromDb.mockReset();
    mockReadOrdersSnapshotFromDb.mockResolvedValue(emptyOrdersSnapshot);
    const mod = await import("../app/api/orders/route");
    GET = mod.GET;
  });

  it("returns empty orders when Turso has no rows", async () => {
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.open_orders).toEqual([]);
    expect(body.executed_orders).toEqual([]);
    expect(body.open_count).toBe(0);
    expect(body.executed_count).toBe(0);
  });

  it("returns order data when Turso has rows", async () => {
    const mockOrders = {
      last_sync: "2026-03-05T14:00:00",
      open_orders: [
        {
          orderId: 101,
          permId: 9001,
          symbol: "AAPL",
          contract: { conId: 1, symbol: "AAPL", secType: "STK", strike: null, right: null, expiry: null },
          action: "BUY",
          orderType: "LMT",
          totalQuantity: 100,
          limitPrice: 175.0,
          auxPrice: null,
          status: "Submitted",
          filled: 0,
          remaining: 100,
          avgFillPrice: null,
          tif: "DAY",
        },
      ],
      executed_orders: [],
      open_count: 1,
      executed_count: 0,
    };
    mockReadOrdersSnapshotFromDb.mockResolvedValue(mockOrders);

    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.open_orders).toHaveLength(1);
    expect(body.open_orders[0].symbol).toBe("AAPL");
    expect(body.open_count).toBe(1);
  });
});

// =============================================================================
// POST /api/orders — success paths
// =============================================================================

describe("POST /api/orders", () => {
  let POST: () => Promise<Response>;

  beforeEach(async () => {
    vi.resetModules();
    mockRadonFetch.mockReset();
    mockReadDataFile.mockReset();
    mockReadOrdersSnapshotFromDb.mockReset();
    mockReadOrdersSnapshotFromDb.mockResolvedValue(emptyOrdersSnapshot);
    const mod = await import("../app/api/orders/route");
    POST = mod.POST;
  });

  it("returns 502 when sync fails and no synced Turso snapshot exists", async () => {
    mockRadonFetch.mockRejectedValue(new Error("IB gateway timeout"));

    const res = await POST();
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toBe("Sync failed");
  });

  it("returns refreshed orders on success", async () => {
    const refreshedOrders = {
      last_sync: "2026-03-05T14:35:00",
      open_orders: [],
      executed_orders: [
        {
          execId: "exec-001",
          symbol: "GOOG",
          contract: { conId: 2, symbol: "GOOG", secType: "STK", strike: null, right: null, expiry: null },
          side: "BOT",
          quantity: 50,
          avgPrice: 175.25,
          commission: 1.0,
          realizedPNL: null,
          time: "2026-03-05T14:30:00",
          exchange: "SMART",
        },
      ],
      open_count: 0,
      executed_count: 1,
    };
    mockRadonFetch.mockResolvedValue(refreshedOrders);
    mockReadOrdersSnapshotFromDb.mockResolvedValue(refreshedOrders);

    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.executed_orders).toHaveLength(1);
    expect(body.executed_orders[0].symbol).toBe("GOOG");
    expect(body.executed_count).toBe(1);
  });
});

// =============================================================================
// GET/POST /api/gex — success paths
// =============================================================================

describe("GET /api/gex", () => {
  let GET: () => Promise<Response>;

  beforeEach(async () => {
    vi.resetModules();
    mockReadFile.mockReset();
    const mod = await import("../app/api/gex/route");
    GET = mod.GET;
  });

  it("returns cached GEX payload when file exists", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify({
      scan_time: "2026-04-22T14:00:00Z",
      ticker: "SPX",
      net_gex: 123,
      history: [],
    }));

    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ticker).toBe("SPX");
    expect(body.net_gex).toBe(123);
  });
});

describe("POST /api/gex", () => {
  let POST: () => Promise<Response>;

  beforeEach(async () => {
    vi.resetModules();
    mockReadFile.mockReset();
    mockRadonFetch.mockReset();
    const mod = await import("../app/api/gex/route");
    POST = mod.POST;
  });

  it("returns fresh scan data from FastAPI", async () => {
    mockRadonFetch.mockResolvedValue({
      scan_time: "2026-04-22T14:01:00Z",
      ticker: "SPX",
      net_gex: 456,
      history: [],
    });

    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.net_gex).toBe(456);
  });

  it("falls back to cached data when the fresh scan fails", async () => {
    mockRadonFetch.mockRejectedValue(new Error("upstream down"));
    mockReadFile.mockResolvedValue(JSON.stringify({
      scan_time: "2026-04-22T14:00:00Z",
      ticker: "SPX",
      net_gex: 321,
      history: [],
    }));

    // REL-238 / R-643: the fallback keeps the cached body but must preserve
    // the upstream failure status and stamp it in the body — a 200 +
    // header-only warning hid dead scans from useSyncHook consumers.
    const res = await POST();
    expect(res.status).toBe(502);
    expect(res.headers.get("X-Sync-Warning")).toContain("GEX sync failed");
    const body = await res.json();
    expect(body.net_gex).toBe(321);
    expect(body.is_stale).toBe(true);
    expect(body.scan_succeeded).toBe(false);
  });
});

// =============================================================================
// GET/POST /api/regime — success paths
// =============================================================================

describe("GET /api/regime", () => {
  let GET: () => Promise<Response>;

  beforeEach(async () => {
    vi.resetModules();
    mockReadFile.mockReset();
    mockStat.mockReset();
    mockStat.mockResolvedValue({ mtimeMs: Date.now() - 5_000 });
    mockRadonFetch.mockReset();
    const mod = await import("../app/api/regime/route");
    GET = mod.GET;
  });

  it("returns cached regime payload when file exists", async () => {
    // Window-relative date: a hardcoded past date goes stale and re-arms
    // triggerBackgroundScan() (T-477).
    const sessionDate = mostRecentSessionDate();
    mockReadFile.mockResolvedValue(JSON.stringify({
      scan_time: `${sessionDate}T14:00:00Z`,
      date: sessionDate,
      market_open: true,
      cri: { score: 24, level: "ELEVATED", components: { vix: 6, vvix: 6, correlation: 6, momentum: 6 } },
      history: [],
      spy_closes: [],
    }));

    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.date).toBe(sessionDate);
    expect(body.cri.score).toBe(24);
    // T-477: a fresh cached payload must not fire a background /regime/scan.
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });
});

describe("POST /api/regime", () => {
  let POST: () => Promise<Response>;

  beforeEach(async () => {
    vi.resetModules();
    mockReadFile.mockReset();
    mockRadonFetch.mockReset();
    const mod = await import("../app/api/regime/route");
    POST = mod.POST;
  });

  it("returns fresh regime scan data from FastAPI", async () => {
    mockRadonFetch.mockResolvedValue({
      scan_time: "2026-04-22T14:01:00Z",
      date: "2026-04-22",
      market_open: true,
      cri: { score: 31, level: "HIGH", components: { vix: 10, vvix: 7, correlation: 7, momentum: 7 } },
      history: [],
      spy_closes: [],
    });

    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.cri.score).toBe(31);
  });

  it("falls back to cached regime data when the fresh scan fails", async () => {
    mockRadonFetch.mockRejectedValue(new Error("upstream down"));
    mockReadFile.mockResolvedValue(JSON.stringify({
      scan_time: "2026-04-22T14:00:00Z",
      date: "2026-04-22",
      market_open: true,
      cri: { score: 18, level: "LOW", components: { vix: 4, vvix: 4, correlation: 5, momentum: 5 } },
      history: [],
      spy_closes: [],
    }));

    // REL-238 / R-643: status preserved + body-level failure markers (see the
    // GEX fallback test above for the rationale).
    const res = await POST();
    expect(res.status).toBe(502);
    expect(res.headers.get("X-Sync-Warning")).toContain("CRI sync failed");
    const body = await res.json();
    expect(body.cri.score).toBe(18);
    expect(body.is_stale).toBe(true);
    expect(body.scan_succeeded).toBe(false);
  });
});

// =============================================================================
// GET /api/blotter — success paths
// =============================================================================

describe("GET /api/blotter", () => {
  let GET: () => Promise<Response>;

  beforeEach(async () => {
    vi.resetModules();
    mockReadFile.mockReset();
    mockExecute.mockReset();
    mockExecute.mockResolvedValue({ rows: [] });
    const mod = await import("../app/api/blotter/route");
    GET = mod.GET;
  });

  it("returns journal-derived data when Turso rows exist", async () => {
    mockExecute.mockResolvedValue({
      rows: [
        {
          payload: JSON.stringify({
            id: 1,
            date: "2026-03-05",
            ticker: "AAPL",
            action: "SELL_OPTION",
            fill_price: 5,
            total_cost: 500,
            contracts: 1,
            commission: 2.5,
            realized_pnl: 500,
            ib_exec_id: "AAPL-1",
          }),
          filled_at: "2026-03-05T15:00:00Z",
        },
      ],
    });

    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.summary.closed_trades).toBe(1);
    expect(body.closed_trades).toHaveLength(1);
    expect(body.closed_trades[0].symbol).toBe("AAPL");
  });

  it("returns empty structure when journal is empty", async () => {
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.as_of).toBe("");
    expect(body.summary.closed_trades).toBe(0);
    expect(body.summary.realized_pnl).toBe(0);
    expect(body.closed_trades).toEqual([]);
    expect(body.open_trades).toEqual([]);
  });
});

// =============================================================================
// GET /api/journal — success paths
// =============================================================================

describe("GET /api/journal", () => {
  let GET: () => Promise<Response>;

  beforeEach(async () => {
    vi.resetModules();
    mockRadonFetch.mockReset();
    mockExecute.mockReset();
    mockExecute.mockResolvedValue({ rows: [] });
    const mod = await import("../app/api/journal/route");
    GET = mod.GET;
  });

  it("returns trade data from the Turso journal table", async () => {
    mockJournalDb([
      {
        id: 1,
        date: "2026-03-04",
        ticker: "GOOG",
        structure: "Long Call",
        decision: "ENTER",
        entry_cost: 2500,
      },
      {
        id: 2,
        date: "2026-03-05",
        ticker: "AMD",
        structure: "Bull Call Spread",
        decision: "ENTER",
        entry_cost: 1200,
      },
    ]);

    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.trades).toHaveLength(2);
    expect(body.trades[0].ticker).toBe("GOOG");
    expect(body.trades[1].ticker).toBe("AMD");
  });

  it("returns empty trades when the Turso journal table is empty", async () => {
    mockJournalDb([]);

    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.trades).toEqual([]);
  });

  it("returns 500 with empty trades when the Turso read fails", async () => {
    mockExecute.mockRejectedValue(new Error("DB unavailable"));

    const res = await GET();
    expect(res.status).toBe(500);
    const body = await res.json();
    expect(body.error).toBeDefined();
  });
});

describe("POST /api/journal", () => {
  let POST: () => Promise<Response>;

  beforeEach(async () => {
    vi.resetModules();
    mockRadonFetch.mockReset();
    mockExecute.mockReset();
    mockExecute.mockResolvedValue({ rows: [] });
    const mod = await import("../app/api/journal/route");
    POST = mod.POST;
  });

  it("reconciles, imports latest reconciliation rows to Turso, and returns fresh journal data", async () => {
    mockRadonFetch.mockResolvedValue({ ok: true });
    mockJournalImportDb({
      needs_attention: true,
      timestamp: "2026-04-23T12:00:00Z",
      new_trades: [
        {
          symbol: "PLTR",
          date: "2026-04-23",
          action: "BUY_OPTION",
          net_quantity: 5,
          avg_price: 2.5,
          commission: 1.25,
          realized_pnl: 0,
          sec_type: "OPT",
          strike: 115,
          expiry: "20260517",
          right: "C",
        },
      ],
    });

    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.trades).toHaveLength(1);
    expect(body.trades[0].ticker).toBe("PLTR");
  });

  it("falls back to current Turso journal when reconcile fails", async () => {
    mockRadonFetch.mockRejectedValue(new Error("IB down"));
    mockJournalDb([
      { id: 9, date: "2026-04-23", ticker: "GOOG", structure: "Long Call", decision: "EXECUTED" },
    ]);

    const res = await POST();
    expect(res.status).toBe(200);
    expect(res.headers.get("X-Sync-Warning")).toContain("Journal sync failed");
    const body = await res.json();
    expect(body.trades[0].ticker).toBe("GOOG");
  });
});
