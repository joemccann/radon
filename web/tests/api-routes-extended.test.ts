import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * Extended API route tests — heavy mocking of external services.
 *
 * Each section uses vi.resetModules() + dynamic imports so that
 * per-test mock configuration takes effect on the route module.
 */

// ---------------------------------------------------------------------------
// Top-level mocks (vi.mock is hoisted)
// ---------------------------------------------------------------------------

// Mock fs (seasonality uses `import { promises as fs } from "fs"`)
const mockReadFile = vi.fn();
const mockWriteFile = vi.fn().mockResolvedValue(undefined);
const mockMkdir = vi.fn().mockResolvedValue(undefined);
vi.mock("fs", () => ({
  promises: {
    readFile: mockReadFile,
    writeFile: mockWriteFile,
    mkdir: mockMkdir,
  },
}));

// Mock fs/promises (blotter, discover, journal use `import { readFile } from "fs/promises"`)
vi.mock("fs/promises", () => ({
  readFile: mockReadFile,
  writeFile: mockWriteFile,
  mkdir: mockMkdir,
}));

// Mock @tools/runner for orders/place
const mockRunScript = vi.fn().mockResolvedValue({ ok: false, stderr: "mocked" });
vi.mock("@tools/runner", () => ({
  runScript: mockRunScript,
  resolveProjectRoot: vi.fn().mockReturnValue("/mock/root"),
}));

// Mock @tools/wrappers/ib-order-manage for cancel + modify
const mockIbCancelOrder = vi.fn();
const mockIbModifyOrder = vi.fn();
vi.mock("@tools/wrappers/ib-order-manage", () => ({
  ibCancelOrder: mockIbCancelOrder,
  ibModifyOrder: mockIbModifyOrder,
}));

// Mock @tools/wrappers/ib-orders for post-action refresh
const mockIbOrders = vi.fn().mockResolvedValue({ ok: true, stderr: "" });
vi.mock("@tools/wrappers/ib-orders", () => ({
  ibOrders: mockIbOrders,
}));

// Mock @tools/data-reader for reading orders.json after refresh
const mockReadDataFile = vi.fn().mockResolvedValue({ ok: false, error: "not found" });
vi.mock("@tools/data-reader", () => ({
  readDataFile: mockReadDataFile,
}));

// Mock @tools/schemas/ib-orders (TypeBox schema import)
vi.mock("@tools/schemas/ib-orders", () => ({
  OrdersData: {},
}));

// Mock @/lib/syncMutex (some routes may import it)
vi.mock("@/lib/syncMutex", () => ({
  createSyncMutex: (fn: () => Promise<unknown>) => fn,
}));

// Mock global fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

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

// ---------------------------------------------------------------------------
// Env helpers
// ---------------------------------------------------------------------------

let envBackup: Record<string, string | undefined>;

function saveEnv() {
  envBackup = {
    UW_TOKEN: process.env.UW_TOKEN,
    ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY,
    CLAUDE_CODE_API_KEY: process.env.CLAUDE_CODE_API_KEY,
    CLAUDE_API_KEY: process.env.CLAUDE_API_KEY,
  };
}

function restoreEnv() {
  for (const [k, v] of Object.entries(envBackup)) {
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
}

// =============================================================================
// 1. GET /api/ticker/seasonality
// =============================================================================

describe("GET /api/ticker/seasonality — extended", () => {
  beforeEach(() => {
    vi.resetModules();
    mockReadFile.mockReset();
    mockWriteFile.mockReset();
    mockMkdir.mockReset();
    mockFetch.mockReset();
    saveEnv();
    process.env.UW_TOKEN = "test-uw-token";
    process.env.ANTHROPIC_API_KEY = "test-anthropic-key";
  });

  afterEach(() => {
    restoreEnv();
  });

  // Helper to build 12 months of seasonality data
  function makeMonths(yearsValue: number = 20): Array<{
    month: number;
    avg_change: number;
    median_change: number;
    max_change: number;
    min_change: number;
    positive_months_perc: number;
    years: number;
  }> {
    return Array.from({ length: 12 }, (_, i) => ({
      month: i + 1,
      avg_change: 0.02 + i * 0.001,
      median_change: 0.015 + i * 0.001,
      max_change: 0.15,
      min_change: -0.08,
      positive_months_perc: 0.6,
      years: yearsValue,
    }));
  }

  it("returns cached data when cache exists and is not expired", async () => {
    const futureDate = new Date(Date.now() + 86400000 * 30).toISOString();
    const cachedData = makeMonths();
    const cacheEntry = {
      ticker: "AAPL",
      expires: futureDate,
      source: "uw",
      data: cachedData,
    };
    mockReadFile.mockResolvedValue(JSON.stringify(cacheEntry));

    const { GET } = await import("../app/api/ticker/seasonality/route");
    const res = await GET(new Request("http://localhost/api/ticker/seasonality?ticker=AAPL"));
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.source).toBe("uw");
    expect(body.data).toHaveLength(12);
    expect(body.data[0].month).toBe(1);
    // Should not have called fetch since cache was hit
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("returns 400 when ticker param is missing", async () => {
    const { GET } = await import("../app/api/ticker/seasonality/route");
    const res = await GET(new Request("http://localhost/api/ticker/seasonality"));
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("ticker");
  });

  it("returns 500 when UW_TOKEN is not set", async () => {
    delete process.env.UW_TOKEN;
    // Make cache miss
    mockReadFile.mockRejectedValue(new Error("ENOENT"));

    const { GET } = await import("../app/api/ticker/seasonality/route");
    const res = await GET(new Request("http://localhost/api/ticker/seasonality?ticker=AAPL"));
    expect(res.status).toBe(500);
    const body = await res.json();
    expect(body.error).toContain("UW_TOKEN");
  });

  it("returns UW data when all 12 months are populated", async () => {
    // Cache miss
    mockReadFile.mockRejectedValue(new Error("ENOENT"));

    const uwMonths = makeMonths(20);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ data: uwMonths }),
    });

    const { GET } = await import("../app/api/ticker/seasonality/route");
    const res = await GET(new Request("http://localhost/api/ticker/seasonality?ticker=AAPL"));
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.source).toBe("uw");
    expect(body.data).toHaveLength(12);
    expect(body.data[0].years).toBe(20);
    // Should have written to cache
    expect(mockWriteFile).toHaveBeenCalled();
  });

  it("returns empty data when UW returns empty and Vision image HEAD returns 404", async () => {
    // Cache miss
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    // Clear anthropic key so Vision path would work but we make HEAD fail
    delete process.env.ANTHROPIC_API_KEY;
    delete process.env.CLAUDE_CODE_API_KEY;
    delete process.env.CLAUDE_API_KEY;

    // UW returns empty data
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ data: [] }),
    });
    // extractViaVision: no API key means it returns null without fetching

    const { GET } = await import("../app/api/ticker/seasonality/route");
    const res = await GET(new Request("http://localhost/api/ticker/seasonality?ticker=XYZ"));
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.data).toEqual([]);
    expect(body.source).toBe("uw");
  });

  it("merges UW and Vision data when UW is partial", async () => {
    // Cache miss
    mockReadFile.mockRejectedValue(new Error("ENOENT"));

    // UW returns only 6 months (months 1-6 with years > 0, 7-12 with years = 0)
    const partialUW = Array.from({ length: 12 }, (_, i) => ({
      month: i + 1,
      avg_change: 0.03,
      median_change: 0.02,
      max_change: 0.12,
      min_change: -0.05,
      positive_months_perc: 0.65,
      years: i < 6 ? 20 : 0,
    }));

    // Vision returns all 12 months
    const visionMonths = Array.from({ length: 12 }, (_, i) => ({
      month: i + 1,
      avg_change: 0.04,
      median_change: 0.03,
      max_change: 0.18,
      min_change: -0.09,
      positive_months_perc: 0.55,
      years: 15,
    }));

    // Call 1: UW API
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ data: partialUW }),
    });
    // Call 2: HEAD check for equityclock image
    mockFetch.mockResolvedValueOnce({ ok: true });
    // Call 3: Anthropic Vision API
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        content: [{ type: "text", text: JSON.stringify(visionMonths) }],
      }),
    });

    const { GET } = await import("../app/api/ticker/seasonality/route");
    const res = await GET(new Request("http://localhost/api/ticker/seasonality?ticker=MSFT"));
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.source).toBe("uw+equityclock");
    expect(body.data).toHaveLength(12);
    // Months 1-6: should use UW data (years=20)
    expect(body.data[0].years).toBe(20);
    expect(body.data[5].years).toBe(20);
    // Months 7-12: should use Vision data (years=15)
    expect(body.data[6].years).toBe(15);
    expect(body.data[11].years).toBe(15);
  });
});

// =============================================================================
// 2. GET /api/ticker/news
// =============================================================================

describe("GET /api/ticker/news — extended", () => {
  beforeEach(() => {
    vi.resetModules();
    mockFetch.mockReset();
    saveEnv();
    process.env.UW_TOKEN = "test-uw-token";
  });

  afterEach(() => {
    restoreEnv();
  });

  it("returns 400 when ticker param is missing", async () => {
    const { GET } = await import("../app/api/ticker/news/route");
    const res = await GET(new Request("http://localhost/api/ticker/news"));
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("ticker");
  });

  it("returns UW news when UW_TOKEN is set and fetch succeeds", async () => {
    const uwNews = [
      { headline: "AAPL beats earnings", source: "Reuters", created_at: "2026-03-05T10:00:00Z" },
      { headline: "AAPL expands AI division", source: "Bloomberg", created_at: "2026-03-05T09:00:00Z" },
    ];

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ data: uwNews }),
    });

    const { GET } = await import("../app/api/ticker/news/route");
    const res = await GET(new Request("http://localhost/api/ticker/news?ticker=AAPL"));
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.source).toBe("unusualwhales");
    expect(body.data).toHaveLength(2);
    expect(body.data[0].headline).toBe("AAPL beats earnings");
  });

  it("falls through to Yahoo when UW fails", async () => {
    // UW fetch fails
    mockFetch.mockResolvedValueOnce({ ok: false, json: async () => ({}) });
    // Yahoo chart endpoint — triggers fallthrough to Yahoo search
    mockFetch.mockResolvedValueOnce({ ok: false, json: async () => ({}) });
    // Yahoo search endpoint
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        news: [
          {
            title: "GOOG announces new product",
            publisher: "TechCrunch",
            providerPublishTime: 1741176000,
            link: "https://example.com/article",
          },
        ],
      }),
    });

    const { GET } = await import("../app/api/ticker/news/route");
    const res = await GET(new Request("http://localhost/api/ticker/news?ticker=GOOG"));
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.source).toBe("yahoo");
    expect(body.data).toHaveLength(1);
    expect(body.data[0].headline).toBe("GOOG announces new product");
  });

  it("returns { data: [], source: 'none' } when all sources fail", async () => {
    // UW fails
    mockFetch.mockResolvedValueOnce({ ok: false, json: async () => ({}) });
    // Yahoo chart fails
    mockFetch.mockResolvedValueOnce({ ok: false, json: async () => ({}) });
    // Yahoo search fails
    mockFetch.mockResolvedValueOnce({ ok: false, json: async () => ({}) });

    const { GET } = await import("../app/api/ticker/news/route");
    const res = await GET(new Request("http://localhost/api/ticker/news?ticker=XYZ"));
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.data).toEqual([]);
    expect(body.source).toBe("none");
  });

  it("returns { data: [], source: 'none' } when UW_TOKEN not set and Yahoo fails", async () => {
    delete process.env.UW_TOKEN;
    // Yahoo chart fails
    mockFetch.mockResolvedValueOnce({ ok: false, json: async () => ({}) });
    // Yahoo search fails
    mockFetch.mockResolvedValueOnce({ ok: false, json: async () => ({}) });

    const { GET } = await import("../app/api/ticker/news/route");
    const res = await GET(new Request("http://localhost/api/ticker/news?ticker=XYZ"));
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.data).toEqual([]);
    expect(body.source).toBe("none");
  });
});

// =============================================================================
// 3. POST /api/previous-close
// =============================================================================

describe("POST /api/previous-close — extended", () => {
  beforeEach(() => {
    vi.resetModules();
    mockFetch.mockReset();
    saveEnv();
    process.env.UW_TOKEN = "test-uw-token";
  });

  afterEach(() => {
    restoreEnv();
  });

  it("returns closes from UW when available", async () => {
    mockFetch.mockImplementation(async (url: string | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("unusualwhales.com")) {
        return {
          ok: true,
          json: async () => ({ data: { previous_close: 182.52 } }),
        };
      }
      return { ok: false, json: async () => ({}) };
    });

    const { POST } = await import("../app/api/previous-close/route");
    const res = await POST(
      new Request("http://localhost/api/previous-close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: ["AAPL"] }),
      }),
    );
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.closes.AAPL).toBe(182.52);
  });

  it("falls back to Yahoo when UW fails", async () => {
    delete process.env.UW_TOKEN;

    mockFetch.mockImplementation(async (url: string | URL) => {
      const urlStr = typeof url === "string" ? url : url.toString();
      if (urlStr.includes("yahoo")) {
        return {
          ok: true,
          json: async () => ({
            chart: {
              result: [{ meta: { chartPreviousClose: 175.30 } }],
            },
          }),
        };
      }
      return { ok: false, json: async () => ({}) };
    });

    const { POST } = await import("../app/api/previous-close/route");
    const res = await POST(
      new Request("http://localhost/api/previous-close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: ["MSFT"] }),
      }),
    );
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.closes.MSFT).toBe(175.30);
  });

  it("caches results — second call for same symbol does not re-fetch", async () => {
    let fetchCallCount = 0;
    mockFetch.mockImplementation(async () => {
      fetchCallCount++;
      return {
        ok: true,
        json: async () => ({ data: { previous_close: 99.99 } }),
      };
    });

    const { POST } = await import("../app/api/previous-close/route");

    // First call
    const res1 = await POST(
      new Request("http://localhost/api/previous-close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: ["CACHE_TEST"] }),
      }),
    );
    expect(res1.status).toBe(200);
    const body1 = await res1.json();
    expect(body1.closes.CACHE_TEST).toBe(99.99);
    const callsAfterFirst = fetchCallCount;

    // Second call — should use cache
    const res2 = await POST(
      new Request("http://localhost/api/previous-close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: ["CACHE_TEST"] }),
      }),
    );
    expect(res2.status).toBe(200);
    const body2 = await res2.json();
    expect(body2.closes.CACHE_TEST).toBe(99.99);
    // No additional fetch calls
    expect(fetchCallCount).toBe(callsAfterFirst);
  });

  it("returns empty closes for empty symbols array", async () => {
    const { POST } = await import("../app/api/previous-close/route");
    const res = await POST(
      new Request("http://localhost/api/previous-close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: [] }),
      }),
    );
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ closes: {} });
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

// =============================================================================
// 4. POST /api/orders/cancel
// =============================================================================

describe("POST /api/orders/cancel — extended", () => {
  beforeEach(() => {
    vi.resetModules();
    mockRadonFetch.mockReset();
    mockReadDataFile.mockReset();
    mockReadDataFile.mockResolvedValue({
      ok: true,
      data: { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 },
    });
  });

  it("returns success when cancel succeeds", async () => {
    mockRadonFetch
      .mockResolvedValueOnce({ status: "ok", message: "Order 101 cancelled" })
      .mockResolvedValueOnce({});

    const { POST } = await import("../app/api/orders/cancel/route");
    const res = await POST(
      new Request("http://localhost/api/orders/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orderId: 101 }),
      }),
    );
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.status).toBe("ok");
    expect(body.message).toBe("Order 101 cancelled");
    expect(body.orders).toBeDefined();
  });

  it("returns 500 when cancel fails via radonFetch", async () => {
    mockRadonFetch.mockRejectedValue(new Error("conn refused"));

    const { POST } = await import("../app/api/orders/cancel/route");
    const res = await POST(
      new Request("http://localhost/api/orders/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orderId: 202 }),
      }),
    );
    expect(res.status).toBe(500);

    const body = await res.json();
    expect(body.error).toContain("conn refused");
  });

  it("returns 400 when neither orderId nor permId provided", async () => {
    const { POST } = await import("../app/api/orders/cancel/route");
    const res = await POST(
      new Request("http://localhost/api/orders/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }),
    );
    expect(res.status).toBe(400);

    const body = await res.json();
    expect(body.error).toContain("orderId");
  });
});

// =============================================================================
// 5. POST /api/orders/modify
// =============================================================================

describe("POST /api/orders/modify — extended", () => {
  beforeEach(() => {
    vi.resetModules();
    mockRadonFetch.mockReset();
    mockReadDataFile.mockReset();
    mockReadDataFile.mockResolvedValue({
      ok: true,
      data: { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 },
    });
  });

  it("returns success when modify succeeds", async () => {
    mockRadonFetch
      .mockResolvedValueOnce({ status: "ok", message: "Order 101 modified to 5.50" })
      .mockResolvedValueOnce({});

    const { POST } = await import("../app/api/orders/modify/route");
    const res = await POST(
      new Request("http://localhost/api/orders/modify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orderId: 101, newPrice: 5.50 }),
      }),
    );
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.status).toBe("ok");
    expect(body.message).toBe("Order 101 modified to 5.50");
    expect(body.orders).toBeDefined();
  });

  it("returns 500 when modify fails via radonFetch", async () => {
    mockRadonFetch.mockRejectedValue(new Error("connection timeout"));

    const { POST } = await import("../app/api/orders/modify/route");
    const res = await POST(
      new Request("http://localhost/api/orders/modify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orderId: 202, newPrice: 3.00 }),
      }),
    );
    expect(res.status).toBe(500);

    const body = await res.json();
    expect(body.error).toContain("connection timeout");
  });

  it("returns 400 when neither orderId nor permId provided", async () => {
    const { POST } = await import("../app/api/orders/modify/route");
    const res = await POST(
      new Request("http://localhost/api/orders/modify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ newPrice: 5.00 }),
      }),
    );
    expect(res.status).toBe(400);

    const body = await res.json();
    expect(body.error).toContain("orderId");
  });

  it("returns 400 when newPrice is missing", async () => {
    const { POST } = await import("../app/api/orders/modify/route");
    const res = await POST(
      new Request("http://localhost/api/orders/modify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ orderId: 101 }),
      }),
    );
    expect(res.status).toBe(400);

    const body = await res.json();
    expect(body.error).toContain("newPrice");
  });
});

// =============================================================================
// 6. POST /api/orders/place
// =============================================================================

describe("POST /api/orders/place — extended", () => {
  beforeEach(() => {
    vi.resetModules();
    mockRadonFetch.mockReset();
    mockReadDataFile.mockReset();
    mockReadDataFile.mockResolvedValue({
      ok: true,
      data: { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 },
    });
  });

  it("returns success with orderId when placement succeeds", async () => {
    mockRadonFetch
      .mockResolvedValueOnce({
        status: "ok",
        orderId: 12345,
        permId: 67890,
        initialStatus: "Submitted",
        message: "Order placed successfully",
      })
      .mockResolvedValueOnce({});

    const { POST } = await import("../app/api/orders/place/route");
    const res = await POST(
      new Request("http://localhost/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "stock",
          symbol: "AAPL",
          action: "BUY",
          quantity: 100,
          limitPrice: 175.00,
        }),
      }),
    );
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.status).toBe("ok");
    expect(body.orderId).toBe(12345);
    expect(body.permId).toBe(67890);
    expect(body.initialStatus).toBe("Submitted");
    expect(body.orders).toBeDefined();
  });

  it("returns 500 when radonFetch fails", async () => {
    mockRadonFetch.mockRejectedValue(new Error("IB Gateway not running"));

    const { POST } = await import("../app/api/orders/place/route");
    const res = await POST(
      new Request("http://localhost/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "stock",
          symbol: "GOOG",
          action: "BUY",
          quantity: 50,
          limitPrice: 180.00,
        }),
      }),
    );
    expect(res.status).toBe(500);

    const body = await res.json();
    expect(body.error).toContain("IB Gateway");
  });

  it("returns 400 when required fields are missing", async () => {
    const { POST } = await import("../app/api/orders/place/route");
    const res = await POST(
      new Request("http://localhost/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }),
    );
    expect(res.status).toBe(400);
  });

  it("passes option fields through to radonFetch correctly", async () => {
    mockRadonFetch
      .mockResolvedValueOnce({
        status: "ok",
        orderId: 55555,
        permId: 88888,
        initialStatus: "PreSubmitted",
        message: "Option order placed",
      })
      .mockResolvedValueOnce({});

    const { POST } = await import("../app/api/orders/place/route");
    const res = await POST(
      new Request("http://localhost/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "option",
          symbol: "AAPL",
          action: "BUY",
          quantity: 5,
          limitPrice: 3.50,
          expiry: "20260320",
          strike: 200,
          right: "C",
        }),
      }),
    );
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.orderId).toBe(55555);

    // Verify radonFetch was called with order payload containing option fields
    const [, opts] = mockRadonFetch.mock.calls[0];
    const payload = JSON.parse(opts.body);
    expect(payload.expiry).toBe("20260320");
    expect(payload.strike).toBe(200);
    expect(payload.right).toBe("C");
    expect(payload.symbol).toBe("AAPL");
  });
});

// =============================================================================
// 6b. POST /api/orders/place — silent IB rejection (SPXU combo bug)
// =============================================================================

describe("POST /api/orders/place — silent IB rejection states", () => {
  const SPXU_COMBO_BODY = {
    type: "combo",
    symbol: "SPXU",
    action: "SELL",
    quantity: 20,
    limitPrice: 2.25,
    tif: "GTC",
    legs: [
      { expiry: "20260313", strike: 53, right: "C", action: "SELL", ratio: 1 },
      { expiry: "20260313", strike: 60, right: "C", action: "BUY", ratio: 1 },
    ],
  };

  beforeEach(() => {
    vi.resetModules();
    mockRadonFetch.mockReset();
    mockReadDataFile.mockReset();
    mockReadDataFile.mockResolvedValue({
      ok: true,
      data: { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 },
    });
  });

  it("returns 502 when IB silently cancels the order (Cancelled status)", async () => {
    mockRadonFetch.mockResolvedValueOnce({
      status: "ok",
      orderId: 12345,
      permId: 67890,
      initialStatus: "Cancelled",
      message: "SELL 20 SPXU @ $2.25 — Cancelled",
    });

    const { POST } = await import("../app/api/orders/place/route");
    const res = await POST(
      new Request("http://localhost/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(SPXU_COMBO_BODY),
      }),
    );
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toMatch(/Cancelled/i);
  });

  it("returns 502 when IB reports ApiCancelled", async () => {
    mockRadonFetch.mockResolvedValueOnce({
      status: "ok",
      orderId: 12345,
      permId: 67890,
      initialStatus: "ApiCancelled",
    });

    const { POST } = await import("../app/api/orders/place/route");
    const res = await POST(
      new Request("http://localhost/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(SPXU_COMBO_BODY),
      }),
    );
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toMatch(/ApiCancelled/i);
  });

  it("returns 502 when IB returns Unknown (no ack before disconnect)", async () => {
    mockRadonFetch.mockResolvedValueOnce({
      status: "ok",
      orderId: 0,
      permId: 0,
      initialStatus: "Unknown",
    });

    const { POST } = await import("../app/api/orders/place/route");
    const res = await POST(
      new Request("http://localhost/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(SPXU_COMBO_BODY),
      }),
    );
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toMatch(/Unknown|no acknowledgement/i);
  });

  it("returns 502 when IB returns Inactive", async () => {
    mockRadonFetch.mockResolvedValueOnce({
      status: "ok",
      orderId: 12345,
      permId: 67890,
      initialStatus: "Inactive",
    });

    const { POST } = await import("../app/api/orders/place/route");
    const res = await POST(
      new Request("http://localhost/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(SPXU_COMBO_BODY),
      }),
    );
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toMatch(/Inactive/i);
  });

  it("returns 200 when IB accepts the combo order (Submitted)", async () => {
    mockRadonFetch
      .mockResolvedValueOnce({
        status: "ok",
        orderId: 12345,
        permId: 67890,
        initialStatus: "Submitted",
        message: "SELL 20 SPXU @ $2.25 — Submitted",
      })
      .mockResolvedValueOnce({});

    const { POST } = await import("../app/api/orders/place/route");
    const res = await POST(
      new Request("http://localhost/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(SPXU_COMBO_BODY),
      }),
    );
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("ok");
    expect(body.initialStatus).toBe("Submitted");
  });

  it("returns 200 when IB accepts the combo order (PreSubmitted)", async () => {
    mockRadonFetch
      .mockResolvedValueOnce({
        status: "ok",
        orderId: 12345,
        permId: 67890,
        initialStatus: "PreSubmitted",
      })
      .mockResolvedValueOnce({});

    const { POST } = await import("../app/api/orders/place/route");
    const res = await POST(
      new Request("http://localhost/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(SPXU_COMBO_BODY),
      }),
    );
    expect(res.status).toBe(200);
  });

  it("passes combo legs to FastAPI correctly", async () => {
    mockRadonFetch
      .mockResolvedValueOnce({
        status: "ok",
        orderId: 12345,
        permId: 67890,
        initialStatus: "Submitted",
      })
      .mockResolvedValueOnce({});

    const { POST } = await import("../app/api/orders/place/route");
    await POST(
      new Request("http://localhost/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(SPXU_COMBO_BODY),
      }),
    );

    // Verify radonFetch was called with order payload containing combo legs
    const [, opts] = mockRadonFetch.mock.calls[0];
    const parsed = JSON.parse(opts.body);
    expect(parsed.type).toBe("combo");
    expect(parsed.symbol).toBe("SPXU");
    expect(parsed.legs).toHaveLength(2);
    expect(parsed.legs[0].strike).toBe(53);
    expect(parsed.legs[1].strike).toBe(60);
    expect(parsed.legs[0].right).toBe("C");
  });
});

// =============================================================================
// 7. GET /api/ticker/ratings — mocked runScript
// =============================================================================

describe("GET /api/ticker/ratings — extended", () => {
  beforeEach(() => {
    vi.resetModules();
    mockRunScript.mockReset();
  });

  it("returns ratings data when script succeeds", async () => {
    mockRunScript.mockResolvedValue({
      ok: true,
      data: {
        ticker: "AAPL",
        consensus: "Buy",
        buy_count: 25,
        hold_count: 5,
        sell_count: 1,
        price_target_avg: 200,
      },
    });

    const { GET } = await import("../app/api/ticker/ratings/route");
    const res = await GET(new Request("http://localhost/api/ticker/ratings?ticker=AAPL"));
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.ticker).toBe("AAPL");
    expect(body.consensus).toBe("Buy");
  });

  it("returns 502 when script fails", async () => {
    mockRunScript.mockResolvedValue({
      ok: false,
      exitCode: 1,
      stderr: "UW API error",
    });

    const { GET } = await import("../app/api/ticker/ratings/route");
    const res = await GET(new Request("http://localhost/api/ticker/ratings?ticker=XYZ"));
    expect(res.status).toBe(502);

    const body = await res.json();
    expect(body.error).toContain("Failed to fetch ratings");
  });

  it("returns 400 when ticker param is missing", async () => {
    const { GET } = await import("../app/api/ticker/ratings/route");
    const res = await GET(new Request("http://localhost/api/ticker/ratings"));
    expect(res.status).toBe(400);
  });
});

// =============================================================================
// 8. POST /api/assistant — mocked Claude API
// =============================================================================

describe("POST /api/assistant — extended", () => {
  beforeEach(() => {
    vi.resetModules();
    mockFetch.mockReset();
    saveEnv();
  });

  afterEach(() => {
    restoreEnv();
  });

  it("returns mock response when ASSISTANT_MOCK=1", async () => {
    process.env.ASSISTANT_MOCK = "1";

    const { POST } = await import("../app/api/assistant/route");
    const res = await POST(
      new Request("http://localhost/api/assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: [{ role: "user", content: "analyze AAPL" }],
        }),
      }) as any,
    );
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.model).toBe("mock");
    expect(body.content).toContain("Mock Claude response");
  });

  it("returns 400 when no messages supplied", async () => {
    process.env.ASSISTANT_MOCK = "1";

    const { POST } = await import("../app/api/assistant/route");
    const res = await POST(
      new Request("http://localhost/api/assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: [] }),
      }) as any,
    );
    expect(res.status).toBe(400);

    const body = await res.json();
    expect(body.error).toContain("No messages");
  });

  it("returns 400 when last message is not from user (non-mock)", async () => {
    process.env.ASSISTANT_MOCK = "0";
    process.env.ANTHROPIC_API_KEY = "test-key";

    const { POST } = await import("../app/api/assistant/route");
    const res = await POST(
      new Request("http://localhost/api/assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: [
            { role: "user", content: "hello" },
            { role: "assistant", content: "hi there" },
          ],
        }),
      }) as any,
    );
    expect(res.status).toBe(400);

    const body = await res.json();
    expect(body.error).toContain("last message must be from user");
  });

  it("calls Anthropic API and returns response", async () => {
    process.env.ASSISTANT_MOCK = "0";
    process.env.ANTHROPIC_API_KEY = "test-key";

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        model: "claude-sonnet-4-5-20250929",
        content: [{ type: "text", text: "AAPL shows strong accumulation" }],
        stop_reason: "end_turn",
        usage: { input_tokens: 50, output_tokens: 20 },
      }),
    });

    const { POST } = await import("../app/api/assistant/route");
    const res = await POST(
      new Request("http://localhost/api/assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: [{ role: "user", content: "analyze AAPL" }],
        }),
      }) as any,
    );
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.content).toBe("AAPL shows strong accumulation");
    expect(body.model).toBe("claude-sonnet-4-5-20250929");
  });

  it("returns 502 when Anthropic API fails", async () => {
    process.env.ASSISTANT_MOCK = "0";
    process.env.ANTHROPIC_API_KEY = "test-key";

    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => "Internal Server Error",
    });

    const { POST } = await import("../app/api/assistant/route");
    const res = await POST(
      new Request("http://localhost/api/assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: [{ role: "user", content: "analyze AAPL" }],
        }),
      }) as any,
    );
    expect(res.status).toBe(502);

    const body = await res.json();
    expect(body.error).toContain("500");
  });

  it("returns 400 for invalid JSON payload (non-mock)", async () => {
    process.env.ASSISTANT_MOCK = "0";
    process.env.ANTHROPIC_API_KEY = "test-key";

    const { POST } = await import("../app/api/assistant/route");
    const res = await POST(
      new Request("http://localhost/api/assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "not json",
      }) as any,
    );
    expect(res.status).toBe(400);

    const body = await res.json();
    expect(body.error).toContain("Invalid JSON");
  });
});

// =============================================================================
// 8. GET /api/blotter — mocked fs
// =============================================================================

describe("GET /api/blotter — extended", () => {
  beforeEach(() => {
    vi.resetModules();
    mockReadFile.mockReset();
  });

  it("returns cached blotter data when file exists", async () => {
    const blotterData = {
      as_of: "2026-03-05",
      summary: { closed_trades: 5, open_trades: 2, total_commissions: 45.50, realized_pnl: 1200 },
      closed_trades: [{ symbol: "AAPL", realized_pnl: 500 }],
      open_trades: [],
    };
    mockReadFile.mockResolvedValue(JSON.stringify(blotterData));

    const { GET } = await import("../app/api/blotter/route");
    const res = await GET();
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.as_of).toBe("2026-03-05");
    expect(body.summary.closed_trades).toBe(5);
    expect(body.closed_trades).toHaveLength(1);
  });

  it("returns empty structure when file not found", async () => {
    mockReadFile.mockRejectedValue(new Error("ENOENT"));

    const { GET } = await import("../app/api/blotter/route");
    const res = await GET();
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.as_of).toBe("");
    expect(body.closed_trades).toEqual([]);
    expect(body.open_trades).toEqual([]);
  });
});

// =============================================================================
// 9. GET /api/discover — mocked fs
// =============================================================================

describe("GET /api/discover — extended", () => {
  beforeEach(() => {
    vi.resetModules();
    mockReadFile.mockReset();
  });

  it("returns cached discover data when file exists", async () => {
    const discoverData = {
      discovery_time: "2026-03-05T10:00:00Z",
      alerts_analyzed: 150,
      candidates_found: 3,
      candidates: [{ ticker: "NET", score: 78 }],
    };
    mockReadFile.mockResolvedValue(JSON.stringify(discoverData));

    const { GET } = await import("../app/api/discover/route");
    const res = await GET();
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.candidates_found).toBe(3);
    expect(body.candidates).toHaveLength(1);
    expect(body.candidates[0].ticker).toBe("NET");
  });

  it("returns empty structure when cache file not found", async () => {
    mockReadFile.mockRejectedValue(new Error("ENOENT"));

    const { GET } = await import("../app/api/discover/route");
    const res = await GET();
    expect(res.status).toBe(200);

    const body = await res.json();
    expect(body.candidates).toEqual([]);
    expect(body.candidates_found).toBe(0);
  });
});
