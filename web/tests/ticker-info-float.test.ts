/**
 * GET /api/ticker/info — short_float (public float) sourcing from
 * UW /api/shorts/{ticker}/interest-float/v2.
 *
 * fs is fully mocked: the route's cacheDir() resolves OUTSIDE the repo
 * (../data/company_info_cache) and real writes would pollute the prod cache.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { readFileMock, writeFileMock, mkdirMock } = vi.hoisted(() => ({
  readFileMock: vi.fn(),
  writeFileMock: vi.fn(async () => undefined),
  mkdirMock: vi.fn(async () => undefined),
}));

vi.mock("fs", () => {
  const promises = {
    readFile: readFileMock,
    writeFile: writeFileMock,
    mkdir: mkdirMock,
  };
  return { promises, default: { promises } };
});

vi.mock("@/lib/demo/enforceAiQuota", () => ({
  enforceDemoAiQuota: async () => null,
}));

vi.mock("@/lib/apiContracts", () => ({
  getRequestId: () => "test-rid",
  jsonApiError: ({ message, status }: { message: string; status: number }) =>
    new Response(JSON.stringify({ error: message }), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  setCacheResponseHeaders: (response: Response) => response,
}));

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const FLOAT_ROW = {
  symbol: "AAPL",
  short_interest: 140_526_320,
  market_date: "2026-06-30",
  short_shares_available: 10_000_000,
  total_float: 14_687_356_000,
  fee_rate: "0.4027",
  rebate_rate: "3.2173",
};

function installFetch(opts: { floatStatus?: number } = {}) {
  const spy = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/interest-float/v2")) {
      if (opts.floatStatus && opts.floatStatus !== 200) {
        return jsonResponse({ error: "boom" }, opts.floatStatus);
      }
      // Newest row first, matching the live endpoint shape.
      return jsonResponse({ data: [FLOAT_ROW, { ...FLOAT_ROW, market_date: "2026-06-15" }] });
    }
    if (url.includes("/stock-state")) {
      return jsonResponse({ data: { open: 210.5, high: 212.0 } });
    }
    if (url.includes("unusualwhales.com") && url.includes("/info")) {
      return jsonResponse({ data: { issue_type: "Common Stock", marketcap: "3000000000000" } });
    }
    return jsonResponse({});
  });
  global.fetch = spy as unknown as typeof fetch;
  return spy;
}

function futureIso(hoursAhead = 12): string {
  return new Date(Date.now() + hoursAhead * 60 * 60 * 1000).toISOString();
}

function makeCacheEntry(overrides: Record<string, unknown> = {}) {
  return {
    ticker: "AAPL",
    profile_expires: null,
    stats_expires: futureIso(),
    uw_info: { issue_type: "Common Stock", marketcap: "3000000000000" },
    stock_state: { open: 209.0 },
    exa_profile: { ceo: "Tim Cook" },
    exa_stats: { pe_ratio: "32" },
    fetched_at: new Date().toISOString(),
    ...overrides,
  };
}

async function callRoute() {
  const { GET } = await import("@/app/api/ticker/info/route");
  return GET(new Request("http://localhost/api/ticker/info?ticker=AAPL"));
}

describe("GET /api/ticker/info short_float", () => {
  const originalFetch = global.fetch;
  const originalUwToken = process.env.UW_TOKEN;
  const originalExaKey = process.env.EXA_API_KEY;

  beforeEach(() => {
    vi.resetModules();
    readFileMock.mockReset();
    writeFileMock.mockClear();
    mkdirMock.mockClear();
    process.env.UW_TOKEN = "test-token";
    delete process.env.EXA_API_KEY;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    if (originalUwToken == null) delete process.env.UW_TOKEN;
    else process.env.UW_TOKEN = originalUwToken;
    if (originalExaKey == null) delete process.env.EXA_API_KEY;
    else process.env.EXA_API_KEY = originalExaKey;
    vi.restoreAllMocks();
  });

  it("cache MISS: fetches interest-float/v2 with Bearer token and returns data[0] as short_float", async () => {
    readFileMock.mockRejectedValue(new Error("ENOENT"));
    const spy = installFetch();

    const response = await callRoute();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.short_float).toEqual(FLOAT_ROW);

    expect(spy).toHaveBeenCalledWith(
      "https://api.unusualwhales.com/api/shorts/AAPL/interest-float/v2",
      expect.objectContaining({
        headers: { Authorization: "Bearer test-token" },
      }),
    );
  });

  it("cache MISS + float endpoint 500: still 200 with short_float {}", async () => {
    readFileMock.mockRejectedValue(new Error("ENOENT"));
    installFetch({ floatStatus: 500 });

    const response = await callRoute();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.short_float).toEqual({});
  });

  it("legacy cache HIT without short_float: fetches float, serves it, and self-heals the cache entry", async () => {
    readFileMock.mockResolvedValue(JSON.stringify(makeCacheEntry()));
    const spy = installFetch();

    const response = await callRoute();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.short_float).toEqual(FLOAT_ROW);

    const floatCalls = spy.mock.calls.filter(([input]) =>
      String(input).includes("/interest-float/v2"),
    );
    expect(floatCalls).toHaveLength(1);

    const persisted = writeFileMock.mock.calls.map(([, content]) =>
      JSON.parse(String(content)),
    );
    expect(
      persisted.some((entry) => entry.short_float?.total_float === FLOAT_ROW.total_float),
    ).toBe(true);
  });

  it("cache HIT with populated short_float: serves from cache without refetching the float endpoint", async () => {
    const cachedFloat = { ...FLOAT_ROW, total_float: 999 };
    readFileMock.mockResolvedValue(
      JSON.stringify(makeCacheEntry({ short_float: cachedFloat })),
    );
    const spy = installFetch();

    const response = await callRoute();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.short_float).toEqual(cachedFloat);

    const floatCalls = spy.mock.calls.filter(([input]) =>
      String(input).includes("/interest-float/v2"),
    );
    expect(floatCalls).toHaveLength(0);
  });
});
