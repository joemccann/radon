/**
 * GET /api/ticker/info — HIT path 15-minute stock-state TTL (U7).
 *
 * A populated cache HIT must not call Unusual Whales until the stock-state
 * TTL expires. uw_info stays on the populated-cache contract (never served
 * empty from this path).
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
  total_float: 14_687_356_000,
  fee_rate: "0.4027",
};

function installFetch() {
  const spy = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/interest-float/v2")) {
      return jsonResponse({ data: [FLOAT_ROW] });
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

function uwCalls(spy: ReturnType<typeof vi.fn>) {
  return spy.mock.calls.filter(([input]) => String(input).includes("unusualwhales.com"));
}

function stockStateCalls(spy: ReturnType<typeof vi.fn>) {
  return spy.mock.calls.filter(([input]) => String(input).includes("/stock-state"));
}

function infoCalls(spy: ReturnType<typeof vi.fn>) {
  return spy.mock.calls.filter(([input]) => {
    const url = String(input);
    return url.includes("unusualwhales.com") && url.includes("/info") && !url.includes("stock-state");
  });
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
    short_float: FLOAT_ROW,
    short_float_checked_at: new Date().toISOString(),
    fetched_at: new Date().toISOString(),
    ...overrides,
  };
}

async function callRoute(ticker = "AAPL") {
  const { GET } = await import("@/app/api/ticker/info/route");
  return GET(new Request(`http://localhost/api/ticker/info?ticker=${ticker}`));
}

describe("GET /api/ticker/info stock-state TTL", () => {
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

  it("warm HIT: second GET for the same symbol does not call unusualwhales", async () => {
    const entry = makeCacheEntry({
      stock_state_checked_at: new Date().toISOString(),
    });
    readFileMock.mockResolvedValue(JSON.stringify(entry));
    const spy = installFetch();

    const first = await callRoute();
    expect(first.status).toBe(200);
    const firstBody = await first.json();
    expect(firstBody.uw_info).toEqual(entry.uw_info);
    expect(Object.keys(firstBody.uw_info).length).toBeGreaterThan(0);
    expect(uwCalls(spy)).toHaveLength(0);

    const second = await callRoute();
    expect(second.status).toBe(200);
    const secondBody = await second.json();
    expect(secondBody.uw_info).toEqual(entry.uw_info);
    expect(secondBody.stock_state).toEqual(entry.stock_state);
    expect(uwCalls(spy)).toHaveLength(0);
  });

  it("HIT with fetched_at inside 15 minutes does not refresh stock-state", async () => {
    readFileMock.mockResolvedValue(JSON.stringify(makeCacheEntry({
      fetched_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    })));
    const spy = installFetch();

    const response = await callRoute();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.uw_info.marketcap).toBe("3000000000000");
    expect(stockStateCalls(spy)).toHaveLength(0);
    expect(infoCalls(spy)).toHaveLength(0);
  });

  it("HIT older than 15 minutes refreshes stock-state only and keeps cached uw_info", async () => {
    const stale = new Date(Date.now() - 16 * 60 * 1000).toISOString();
    readFileMock.mockResolvedValue(JSON.stringify(makeCacheEntry({
      stock_state_checked_at: stale,
      fetched_at: stale,
      short_float_checked_at: new Date().toISOString(),
    })));
    const spy = installFetch();

    const response = await callRoute();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.uw_info).toEqual({
      issue_type: "Common Stock",
      marketcap: "3000000000000",
    });
    expect(body.stock_state).toEqual({ open: 210.5, high: 212.0 });
    expect(stockStateCalls(spy)).toHaveLength(1);
    expect(infoCalls(spy)).toHaveLength(0);
  });

  it("empty cached uw_info is not served on the HIT path", async () => {
    readFileMock.mockResolvedValue(JSON.stringify(makeCacheEntry({
      uw_info: {},
      stock_state_checked_at: new Date().toISOString(),
    })));
    const spy = installFetch();

    const response = await callRoute();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.uw_info).toEqual({
      issue_type: "Common Stock",
      marketcap: "3000000000000",
    });
    expect(infoCalls(spy)).toHaveLength(1);
  });
});
