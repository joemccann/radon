import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * Tests for the FastAPI migration — verifies the new radonFetch-based routes
 * handle success, failure, and cache fallback correctly.
 *
 * Covers:
 * - Scanner/Discover/FlowAnalysis POST: success → fresh data, failure → cached data
 * - Portfolio POST: success → data, failure → cached fallback
 * - Orders POST: coalescing, success → data, failure → cached fallback
 * - Cancel/Modify: input validation preserved, error propagation
 * - Attribution GET: success → data, failure → 500
 * - Blotter POST: success → data, failure → 502
 * - Options chain/expirations: success → data, missing symbol → 400
 */

// ---------------------------------------------------------------------------
// Mocks — must be declared before imports (vi.mock is hoisted)
// ---------------------------------------------------------------------------

// Mock radonFetch — the ONLY external dependency for migrated routes
const mockRadonFetch = vi.fn();
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

// Mock @tools/data-reader for routes that read cached files
const mockReadDataFile = vi.fn().mockResolvedValue({ ok: false, error: "not found" });
vi.mock("@tools/data-reader", () => ({
  readDataFile: mockReadDataFile,
}));

// Mock @tools/schemas/ib-orders and ib-sync (TypeBox schemas)
vi.mock("@tools/schemas/ib-orders", () => ({ OrdersData: {} }));
vi.mock("@tools/schemas/ib-sync", () => ({ PortfolioData: {} }));

// Mock fs/promises for routes that read/write cache files
const mockReadFile = vi.fn();
const mockWriteFile = vi.fn().mockResolvedValue(undefined);
const mockStat = vi.fn().mockResolvedValue({ mtimeMs: Date.now() - 5_000, mtime: new Date() });
const mockStatSync = vi.fn().mockReturnValue({ mtime: new Date() });
vi.mock("fs/promises", () => ({
  readFile: mockReadFile,
  writeFile: mockWriteFile,
  stat: mockStat,
  readdir: vi.fn().mockResolvedValue([]),
  mkdir: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("fs", () => ({
  statSync: mockStatSync,
}));

// Mock criStaleness for regime route
vi.mock("@/lib/criStaleness", () => ({
  isCriDataStale: vi.fn().mockReturnValue(false),
}));
vi.mock("@/lib/criCache", () => ({
  selectPreferredCriCandidate: vi.fn().mockReturnValue(null),
}));
vi.mock("@/lib/regimeHistory", () => ({
  backfillRealizedVolHistory: vi.fn().mockReturnValue([]),
}));
vi.mock("@/lib/performanceFreshness", () => ({
  isPerformanceBehindPortfolioSync: vi.fn().mockReturnValue(false),
  isPortfolioBehindCurrentEtSession: vi.fn().mockReturnValue(false),
}));

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function makeRequest(url: string, init?: RequestInit): Request {
  return new Request(url, init);
}

beforeEach(() => {
  vi.resetModules();
  mockRadonFetch.mockReset();
  mockReadDataFile.mockReset();
  mockReadFile.mockReset();
  mockWriteFile.mockReset();
  mockStat.mockReset();
  mockStatSync.mockReset();
  // Default: fresh stat so no background sync triggers
  mockStat.mockResolvedValue({ mtimeMs: Date.now() - 5_000, mtime: new Date() });
  mockStatSync.mockReturnValue({ mtime: new Date() });
});

// =============================================================================
// POST /api/scanner — success + cache fallback
// =============================================================================

describe("POST /api/scanner (via radonFetch)", () => {
  it("returns data on success", async () => {
    const scanData = { scan_time: "2026-03-14", tickers_scanned: 30, signals_found: 5, top_signals: [] };
    mockRadonFetch.mockResolvedValue(scanData);
    mockStatSync.mockReturnValue({ mtime: new Date() });

    const { POST } = await import("../app/api/scanner/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.tickers_scanned).toBe(30);
    expect(body.cache_meta).toBeDefined();
  });

  it("falls back to cached data on radonFetch failure", async () => {
    mockRadonFetch.mockRejectedValue(new Error("Connection refused"));
    mockReadFile.mockResolvedValue(JSON.stringify({
      scan_time: "2026-03-13", tickers_scanned: 25, signals_found: 3, top_signals: [],
    }));
    mockStatSync.mockReturnValue({ mtime: new Date(Date.now() - 900_000) });

    const { POST } = await import("../app/api/scanner/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.tickers_scanned).toBe(25);
    expect(body.is_stale).toBe(true);
    expect(res.headers.get("X-Sync-Warning")).toContain("cached");
  });

  it("returns 502 on failure when no cache exists", async () => {
    mockRadonFetch.mockRejectedValue(new Error("Connection refused"));
    mockReadFile.mockRejectedValue(new Error("ENOENT"));

    const { POST } = await import("../app/api/scanner/route");
    const res = await POST();
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toBeDefined();
  });
});

// =============================================================================
// POST /api/discover — success + cache fallback
// =============================================================================

describe("POST /api/discover (via radonFetch)", () => {
  it("returns discovery data on success", async () => {
    const data = { discovery_time: "2026-03-14", candidates_found: 12, candidates: [] };
    mockRadonFetch.mockResolvedValue(data);
    mockStatSync.mockReturnValue({ mtime: new Date() });

    const { POST } = await import("../app/api/discover/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.candidates_found).toBe(12);
  });

  it("falls back to cache on failure", async () => {
    mockRadonFetch.mockRejectedValue(new Error("timeout"));
    mockReadFile.mockResolvedValue(JSON.stringify({
      discovery_time: "2026-03-13", candidates_found: 8, candidates: [],
    }));
    mockStatSync.mockReturnValue({ mtime: new Date(Date.now() - 900_000) });

    const { POST } = await import("../app/api/discover/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.candidates_found).toBe(8);
    expect(body.is_stale).toBe(true);
  });
});

// =============================================================================
// POST /api/flow-analysis — success + cache fallback
// =============================================================================

describe("POST /api/flow-analysis (via radonFetch)", () => {
  it("returns flow data on success", async () => {
    const data = { analysis_time: "2026-03-14", positions_scanned: 20, supports: [], against: [] };
    mockRadonFetch.mockResolvedValue(data);
    mockStatSync.mockReturnValue({ mtime: new Date() });

    const { POST } = await import("../app/api/flow-analysis/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.positions_scanned).toBe(20);
  });

  it("falls back to cache on failure", async () => {
    mockRadonFetch.mockRejectedValue(new Error("502"));
    mockReadFile.mockResolvedValue(JSON.stringify({
      analysis_time: "old", positions_scanned: 10, supports: [], against: [],
    }));
    mockStatSync.mockReturnValue({ mtime: new Date(Date.now() - 900_000) });

    const { POST } = await import("../app/api/flow-analysis/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.is_stale).toBe(true);
  });
});

// =============================================================================
// GET /api/attribution — via radonFetch
// =============================================================================

describe("GET /api/attribution (via radonFetch)", () => {
  it("returns attribution data on success", async () => {
    mockRadonFetch.mockResolvedValue({ total_trades: 39, total_realized_pnl: 126927 });

    const { GET } = await import("../app/api/attribution/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.total_trades).toBe(39);
  });

  it("returns 500 on radonFetch failure", async () => {
    mockRadonFetch.mockRejectedValue(new Error("Script exited with code 1"));

    const { GET } = await import("../app/api/attribution/route");
    const res = await GET();
    expect(res.status).toBe(500);
    const body = await res.json();
    expect(body.error).toContain("Script exited");
  });
});

// =============================================================================
// POST /api/portfolio — via radonFetch + cache fallback
// =============================================================================

describe("POST /api/portfolio (via radonFetch)", () => {
  it("returns synced data on success", async () => {
    const portfolio = { bankroll: 100000, last_sync: "2026-03-14T14:30:00", positions: [] };
    mockRadonFetch.mockResolvedValue(portfolio);

    const { POST } = await import("../app/api/portfolio/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.bankroll).toBe(100000);
  });

  it("falls back to cached portfolio when radonFetch fails", async () => {
    mockRadonFetch.mockRejectedValue(new Error("IB connection refused"));
    const cached = { bankroll: 95000, last_sync: "2026-03-13T16:00:00", positions: [{ ticker: "AAPL" }] };
    mockReadDataFile.mockResolvedValue({ ok: true, data: cached });

    const { POST } = await import("../app/api/portfolio/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.bankroll).toBe(95000);
    expect(res.headers.get("X-Sync-Warning")).toContain("cached");
  });

  it("returns 502 when sync fails and no cache exists", async () => {
    mockRadonFetch.mockRejectedValue(new Error("connection refused"));
    mockReadDataFile.mockResolvedValue({ ok: false, error: "not found" });

    const { POST } = await import("../app/api/portfolio/route");
    const res = await POST();
    expect(res.status).toBe(502);
  });
});

// =============================================================================
// GET /api/portfolio — stale-while-revalidate
// =============================================================================

describe("GET /api/portfolio (stale-while-revalidate)", () => {
  it("returns cached data immediately without blocking", async () => {
    const cached = { bankroll: 100000, last_sync: "2026-03-14", positions: [] };
    mockReadDataFile.mockResolvedValue({ ok: true, data: cached });
    // Fresh stat — no background sync
    mockStat.mockResolvedValue({ mtimeMs: Date.now() - 5_000 });

    const { GET } = await import("../app/api/portfolio/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.bankroll).toBe(100000);
  });

  it("returns 404 when no portfolio file exists", async () => {
    mockReadDataFile.mockResolvedValue({ ok: false, error: "File not found" });
    mockStat.mockResolvedValue({ mtimeMs: Date.now() - 5_000 });

    const { GET } = await import("../app/api/portfolio/route");
    const res = await GET();
    expect(res.status).toBe(404);
  });
});

// =============================================================================
// POST /api/orders — via radonFetch + cache fallback
// =============================================================================

describe("POST /api/orders (via radonFetch)", () => {
  it("returns refreshed orders on success", async () => {
    const orders = { last_sync: "2026-03-14", open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 };
    mockRadonFetch.mockResolvedValue(orders);
    mockReadDataFile.mockResolvedValue({ ok: true, data: orders });

    const { POST } = await import("../app/api/orders/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.open_count).toBe(0);
  });

  it("falls back to cached orders when sync fails", async () => {
    mockRadonFetch.mockRejectedValue(new Error("timeout"));
    const cached = { last_sync: "2026-03-13", open_orders: [{ orderId: 1 }], executed_orders: [], open_count: 1, executed_count: 0 };
    mockReadDataFile.mockResolvedValue({ ok: true, data: cached });

    const { POST } = await import("../app/api/orders/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.open_count).toBe(1);
    expect(res.headers.get("X-Sync-Warning")).toContain("cached");
  });

  it("returns 502 when sync fails and no cache", async () => {
    mockRadonFetch.mockRejectedValue(new Error("timeout"));
    mockReadDataFile.mockResolvedValue({ ok: true, data: { last_sync: "", open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 } });

    const { POST } = await import("../app/api/orders/route");
    const res = await POST();
    expect(res.status).toBe(502);
  });
});

// =============================================================================
// POST /api/orders/cancel — input validation preserved
// =============================================================================

describe("POST /api/orders/cancel (via radonFetch)", () => {
  it("returns 400 when both orderId and permId are missing", async () => {
    const { POST } = await import("../app/api/orders/cancel/route");
    const req = makeRequest("http://localhost/api/orders/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("orderId");
  });

  it("succeeds when orderId is provided", async () => {
    mockRadonFetch
      .mockResolvedValueOnce({ status: "ok", message: "Cancelled" })  // cancel
      .mockResolvedValueOnce({});  // refresh
    mockReadDataFile.mockResolvedValue({ ok: true, data: { open_orders: [], executed_orders: [] } });

    const { POST } = await import("../app/api/orders/cancel/route");
    const req = makeRequest("http://localhost/api/orders/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderId: 123 }),
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("ok");
  });
});

// =============================================================================
// POST /api/orders/modify — input validation preserved
// =============================================================================

describe("POST /api/orders/modify (via radonFetch)", () => {
  it("returns 400 when both orderId and permId are missing", async () => {
    const { POST } = await import("../app/api/orders/modify/route");
    const req = makeRequest("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ newPrice: 10.0 }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 when newPrice is missing", async () => {
    const { POST } = await import("../app/api/orders/modify/route");
    const req = makeRequest("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderId: 123 }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("newPrice");
  });

  it("returns 400 when newPrice is zero", async () => {
    const { POST } = await import("../app/api/orders/modify/route");
    const req = makeRequest("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderId: 123, newPrice: 0 }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 when newPrice is negative", async () => {
    const { POST } = await import("../app/api/orders/modify/route");
    const req = makeRequest("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderId: 123, newPrice: -5 }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });
});

// =============================================================================
// POST /api/orders/place — input validation + IB rejection detection
// =============================================================================

describe("POST /api/orders/place (via radonFetch)", () => {
  it("returns 400 when symbol is missing", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "BUY", quantity: 10, limitPrice: 150 }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("detects IB silent rejection (Cancelled status)", async () => {
    mockRadonFetch.mockResolvedValueOnce({
      status: "ok",
      orderId: 42,
      permId: 9999,
      initialStatus: "Cancelled",
      message: "BUY 10 AAPL @ $150",
    });

    const { POST } = await import("../app/api/orders/place/route");
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "stock", symbol: "AAPL", action: "BUY", quantity: 10, limitPrice: 150 }),
    });
    const res = await POST(req);
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toContain("rejected");
  });

  it("detects IB silent rejection (Unknown status)", async () => {
    mockRadonFetch.mockResolvedValueOnce({
      status: "ok",
      orderId: 42,
      permId: 9999,
      initialStatus: "Unknown",
    });

    const { POST } = await import("../app/api/orders/place/route");
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "stock", symbol: "AAPL", action: "BUY", quantity: 10, limitPrice: 150 }),
    });
    const res = await POST(req);
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toContain("no acknowledgement");
  });

  it("succeeds with valid stock order", async () => {
    mockRadonFetch
      .mockResolvedValueOnce({
        status: "ok",
        orderId: 42,
        permId: 9999,
        initialStatus: "Submitted",
        message: "BUY 10 AAPL @ $150.00 — Submitted",
      })
      .mockResolvedValueOnce({});  // orders refresh
    mockReadDataFile.mockResolvedValue({ ok: true, data: { open_orders: [], executed_orders: [] } });

    const { POST } = await import("../app/api/orders/place/route");
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "stock", symbol: "AAPL", action: "BUY", quantity: 10, limitPrice: 150 }),
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.orderId).toBe(42);
    expect(body.initialStatus).toBe("Submitted");
  });

  it("normalizes CALL/PUT combo legs to C/P for FastAPI payload", async () => {
    mockRadonFetch
      .mockResolvedValueOnce({
        status: "ok",
        orderId: 99,
        permId: 100,
        initialStatus: "Submitted",
      })
      .mockResolvedValueOnce({});
    mockReadDataFile
      .mockResolvedValueOnce({ ok: true, data: { positions: [] } })
      .mockResolvedValueOnce({ ok: true, data: { open_orders: [], executed_orders: [] } });

    const { POST } = await import("../app/api/orders/place/route");
    const req = makeRequest("http://localhost/api/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "combo",
        symbol: "AAPL",
        action: "BUY",
        quantity: 1,
        limitPrice: 2.5,
        legs: [
          {
            symbol: "AAPL",
            secType: "OPT",
            expiry: "20260417",
            strike: 100,
            right: "CALL",
            action: "BUY",
            ratio: 1,
          },
          {
            symbol: "AAPL",
            secType: "OPT",
            expiry: "20260417",
            strike: 110,
            right: "CALL",
            action: "SELL",
            ratio: 1,
          },
        ],
      }),
    });
    const res = await POST(req);
    expect(res.status).toBe(200);

    const placeCall = mockRadonFetch.mock.calls.find((c) => c[0] === "/orders/place");
    expect(placeCall).toBeDefined();
    const payload = JSON.parse((placeCall![1] as { body: string }).body) as {
      legs: { right: string; symbol?: string }[];
    };
    expect(payload.legs[0].right).toBe("C");
    expect(payload.legs[1].right).toBe("C");
    expect(payload.legs[0].symbol).toBeUndefined();
  });
});

// =============================================================================
// POST /api/blotter — via radonFetch
// =============================================================================

describe("POST /api/blotter (via radonFetch)", () => {
  it("returns blotter data on success", async () => {
    const data = { as_of: "2026-03-14", summary: { closed_trades: 5 }, closed_trades: [], open_trades: [] };
    mockRadonFetch.mockResolvedValue(data);

    const { POST } = await import("../app/api/blotter/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.summary.closed_trades).toBe(5);
  });

  it("returns 502 on failure", async () => {
    mockRadonFetch.mockRejectedValue(new Error("Flex query timed out"));

    const { POST } = await import("../app/api/blotter/route");
    const res = await POST();
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toContain("timed out");
  });
});

// =============================================================================
// GET /api/options/chain — via radonFetch
// =============================================================================

describe("GET /api/options/chain (via radonFetch)", () => {
  it("returns 400 when symbol is missing", async () => {
    const { GET } = await import("../app/api/options/chain/route");
    const req = makeRequest("http://localhost/api/options/chain");
    const res = await GET(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("symbol");
  });

  it("returns chain data on success", async () => {
    mockRadonFetch.mockResolvedValue({ symbol: "AAPL", expirations: ["2026-04-17"], calls: [], puts: [] });

    const { GET } = await import("../app/api/options/chain/route");
    const req = makeRequest("http://localhost/api/options/chain?symbol=AAPL");
    const res = await GET(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.symbol).toBe("AAPL");
  });

  it("passes expiry parameter when provided", async () => {
    mockRadonFetch.mockResolvedValue({ symbol: "AAPL", calls: [], puts: [] });

    const { GET } = await import("../app/api/options/chain/route");
    const req = makeRequest("http://localhost/api/options/chain?symbol=AAPL&expiry=2026-04-17");
    await GET(req);

    expect(mockRadonFetch).toHaveBeenCalledWith(
      expect.stringContaining("expiry=2026-04-17"),
      expect.any(Object),
    );
  });
});

// =============================================================================
// GET /api/options/expirations — via radonFetch
// =============================================================================

describe("GET /api/options/expirations (via radonFetch)", () => {
  it("returns 400 when symbol is missing", async () => {
    const { GET } = await import("../app/api/options/expirations/route");
    const req = makeRequest("http://localhost/api/options/expirations");
    const res = await GET(req);
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("symbol");
  });

  it("returns expirations on success", async () => {
    mockRadonFetch.mockResolvedValue({ symbol: "GOOG", expirations: ["2026-04-17", "2026-05-15"] });

    const { GET } = await import("../app/api/options/expirations/route");
    const req = makeRequest("http://localhost/api/options/expirations?symbol=GOOG");
    const res = await GET(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.symbol).toBe("GOOG");
    expect(body.expirations).toHaveLength(2);
  });

  it("returns 502 when FastAPI is down", async () => {
    mockRadonFetch.mockRejectedValue(new Error("Connection refused"));

    const { GET } = await import("../app/api/options/expirations/route");
    const req = makeRequest("http://localhost/api/options/expirations?symbol=GOOG");
    const res = await GET(req);
    expect(res.status).toBe(502);
  });
});
