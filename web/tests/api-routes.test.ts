import { describe, it, expect, vi, beforeEach } from "vitest";

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

// Mock @tools/data-reader for portfolio + orders routes
const mockReadDataFile = vi.fn().mockResolvedValue({ ok: false, error: "not found" });
vi.mock("@tools/data-reader", () => ({
  readDataFile: mockReadDataFile,
}));

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
// Default stat: mtime 5 s ago (fresh) so portfolio GET doesn't trigger background spawn
const mockStat = vi.fn().mockResolvedValue({ mtimeMs: Date.now() - 5_000 });
vi.mock("fs/promises", () => ({
  readFile: mockReadFile,
  writeFile: vi.fn().mockResolvedValue(undefined),
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

  it("returns { closes: {} } for empty symbols array", async () => {
    const req = makeRequest("http://localhost/api/previous-close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols: [] }),
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ closes: {} });
  });

  it("returns { closes: {} } when symbols is not an array", async () => {
    const req = makeRequest("http://localhost/api/previous-close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols: "AAPL" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ closes: {} });
  });

  it("returns { closes: {} } when symbols field is missing", async () => {
    const req = makeRequest("http://localhost/api/previous-close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ closes: {} });
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
    const mod = await import("../app/api/portfolio/route");
    GET = mod.GET;
  });

  it("returns portfolio data when file exists", async () => {
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
    mockReadDataFile.mockResolvedValue({ ok: true, data: mockPortfolio });

    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.bankroll).toBe(100000);
    expect(body.positions).toEqual([]);
    expect(body.position_count).toBe(0);
  });

  it("returns 404 when file not found", async () => {
    mockReadDataFile.mockResolvedValue({ ok: false, error: "File not found: data/portfolio.json" });

    const res = await GET();
    expect(res.status).toBe(404);
    const body = await res.json();
    expect(body.error).toContain("not found");
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
    mockReadDataFile.mockResolvedValue({ ok: false, error: "not found" });

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
    const mod = await import("../app/api/orders/route");
    GET = mod.GET;
  });

  it("returns empty orders when no file exists", async () => {
    mockReadDataFile.mockResolvedValue({ ok: false, error: "not found" });

    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.open_orders).toEqual([]);
    expect(body.executed_orders).toEqual([]);
    expect(body.open_count).toBe(0);
    expect(body.executed_count).toBe(0);
  });

  it("returns order data when file exists", async () => {
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
    mockReadDataFile.mockResolvedValue({ ok: true, data: mockOrders });

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
    const mod = await import("../app/api/orders/route");
    POST = mod.POST;
  });

  it("returns 502 when sync fails and no cache", async () => {
    mockRadonFetch.mockRejectedValue(new Error("IB gateway timeout"));
    mockReadDataFile.mockResolvedValue({
      ok: true,
      data: { last_sync: "", open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 },
    });

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
    mockReadDataFile.mockResolvedValue({ ok: true, data: refreshedOrders });

    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.executed_orders).toHaveLength(1);
    expect(body.executed_orders[0].symbol).toBe("GOOG");
    expect(body.executed_count).toBe(1);
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
    const mod = await import("../app/api/blotter/route");
    GET = mod.GET;
  });

  it("returns cached data when file exists", async () => {
    const blotterData = {
      as_of: "2026-03-05",
      summary: { closed_trades: 5, open_trades: 3, total_commissions: 12.50, realized_pnl: 2500 },
      closed_trades: [
        {
          symbol: "AAPL",
          contract_desc: "AAPL 220321C00200000",
          sec_type: "OPT",
          is_closed: true,
          net_quantity: 0,
          total_commission: 2.50,
          realized_pnl: 500,
          cost_basis: 1000,
          proceeds: 1500,
          total_cash_flow: 500,
          executions: [],
        },
      ],
      open_trades: [],
    };
    mockReadFile.mockResolvedValue(JSON.stringify(blotterData));

    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.summary.closed_trades).toBe(5);
    expect(body.closed_trades).toHaveLength(1);
    expect(body.closed_trades[0].symbol).toBe("AAPL");
  });

  it("returns empty structure when file not found", async () => {
    mockReadFile.mockRejectedValue(new Error("ENOENT: no such file or directory"));

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
    mockReadFile.mockReset();
    const mod = await import("../app/api/journal/route");
    GET = mod.GET;
  });

  it("returns trade data when file exists", async () => {
    const tradeLog = {
      trades: [
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
      ],
    };
    mockReadFile.mockResolvedValue(JSON.stringify(tradeLog));

    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.trades).toHaveLength(2);
    expect(body.trades[0].ticker).toBe("GOOG");
    expect(body.trades[1].ticker).toBe("AMD");
  });

  it("returns 500 with empty trades when file not found", async () => {
    mockReadFile.mockRejectedValue(new Error("ENOENT: no such file or directory"));

    const res = await GET();
    expect(res.status).toBe(500);
    const body = await res.json();
    expect(body.trades).toEqual([]);
    expect(body.error).toBeDefined();
  });
});
