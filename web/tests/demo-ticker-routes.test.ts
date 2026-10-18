/**
 * @vitest-environment node
 *
 * The demo deployment carries no UW_TOKEN and no persistent disk, so the
 * ticker company and seasonality reads are completed at request time once the
 * principal resolves to demo. The sample must never reach Unusual Whales, Exa,
 * Yahoo, the Anthropic API, the disk cache, or the demo AI quota.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildDemoTickerInfo, buildDemoSeasonality } from "@/lib/demo/fixtures/tickerInfo";

const mocks = vi.hoisted(() => ({
  requireRouteAccess: vi.fn(),
  enforceDemoAiQuota: vi.fn(),
  readFile: vi.fn(),
  writeFile: vi.fn(),
  mkdir: vi.fn(),
  fetch: vi.fn(),
}));

vi.mock("@/lib/routeAccess", () => ({ requireRouteAccess: mocks.requireRouteAccess }));
vi.mock("@/lib/demo/enforceAiQuota", () => ({ enforceDemoAiQuota: mocks.enforceDemoAiQuota }));
vi.mock("fs", () => {
  const promises = { readFile: mocks.readFile, writeFile: mocks.writeFile, mkdir: mocks.mkdir };
  return { promises, default: { promises } };
});

const NOW = new Date("2026-10-14T15:00:00.000Z");
const ORIGINAL_TOKEN = process.env.UW_TOKEN;

beforeEach(() => {
  vi.resetModules();
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.requireRouteAccess.mockResolvedValue({ ok: true, principal: { userId: "demo-user", kind: "demo" } });
  mocks.enforceDemoAiQuota.mockResolvedValue(null);
  mocks.readFile.mockRejectedValue(new Error("disk must not be reached"));
  mocks.fetch.mockRejectedValue(new Error("network must not be reached"));
  vi.stubGlobal("fetch", mocks.fetch);
  delete process.env.UW_TOKEN;
});

afterEach(() => {
  vi.unstubAllGlobals();
  if (ORIGINAL_TOKEN === undefined) delete process.env.UW_TOKEN;
  else process.env.UW_TOKEN = ORIGINAL_TOKEN;
});

function expectNoUpstreamWork(): void {
  expect(mocks.fetch).not.toHaveBeenCalled();
  expect(mocks.readFile).not.toHaveBeenCalled();
  expect(mocks.writeFile).not.toHaveBeenCalled();
  expect(mocks.mkdir).not.toHaveBeenCalled();
  expect(mocks.enforceDemoAiQuota).not.toHaveBeenCalled();
}

describe("demo ticker info fixture", () => {
  it("is deterministic for a ticker and clock", () => {
    expect(buildDemoTickerInfo("aapl", NOW)).toEqual(buildDemoTickerInfo("AAPL", NOW));
  });

  it("fills every section the company tab reads and marks itself sample data", () => {
    const info = buildDemoTickerInfo("NVDA", NOW);
    expect(info.source).toBe("Sample data");
    expect(info.uw_info.issue_type).toBe("Common Stock");
    expect(Number(info.uw_info.marketcap)).toBeGreaterThan(0);
    expect(String(info.uw_info.description)).toMatch(/sample/i);
    expect(info.profile.ceo).toBeTruthy();
    expect(info.stats.week_52_high).toBeGreaterThan(info.stats.week_52_low as number);
    expect(info.short_float.total_float).toBeGreaterThan(0);
    const state = info.stock_state as Record<string, number>;
    expect(state.high).toBeGreaterThanOrEqual(Math.max(state.open, state.close));
    expect(state.low).toBeLessThanOrEqual(Math.min(state.open, state.close));
    expect(state.prev_close).toBeGreaterThan(0);
  });

  it("classifies funds and indexes so equity-only rows hide", () => {
    expect(buildDemoTickerInfo("SPY", NOW).uw_info.issue_type).toBe("ETF");
    expect(buildDemoTickerInfo("SPX", NOW).uw_info.issue_type).toBe("Index");
  });
});

describe("demo seasonality fixture", () => {
  it("returns twelve populated months with sample provenance", () => {
    const sample = buildDemoSeasonality("TSLA");
    expect(sample.source).toBe("sample");
    expect(sample.data.map((m) => m.month)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
    expect(sample.data.every((m) => m.years > 0 && m.max_change >= m.min_change)).toBe(true);
    expect(buildDemoSeasonality("tsla")).toEqual(sample);
  });
});

describe("GET /api/ticker/info", () => {
  it("serves demo principals a request-time sample without UW_TOKEN", async () => {
    const { GET } = await import("../app/api/ticker/info/route");
    const response = await GET(new Request("http://localhost/api/ticker/info?ticker=aapl"));
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toContain("no-store");
    const body = await response.json();
    expect(body.source).toBe("Sample data");
    expect(body.uw_info.name).toContain("AAPL");
    expect(body.stock_state.close).toBeGreaterThan(0);
    expectNoUpstreamWork();
  });

  it("still validates the ticker before serving the sample", async () => {
    const { GET } = await import("../app/api/ticker/info/route");
    const response = await GET(new Request("http://localhost/api/ticker/info?ticker=../etc"));
    expect(response.status).toBe(400);
    expectNoUpstreamWork();
  });

  it("keeps the operator config error when UW_TOKEN is absent", async () => {
    mocks.requireRouteAccess.mockResolvedValue({ ok: true, principal: { userId: "op", kind: "operator" } });
    mocks.readFile.mockRejectedValue(new Error("ENOENT"));
    const { GET } = await import("../app/api/ticker/info/route");
    const response = await GET(new Request("http://localhost/api/ticker/info?ticker=AAPL"));
    expect(response.status).toBe(500);
    expect(mocks.readFile).toHaveBeenCalled();
  });
});

describe("GET /api/ticker/seasonality", () => {
  it("serves demo principals a sample without charging the AI quota", async () => {
    const { GET } = await import("../app/api/ticker/seasonality/route");
    const response = await GET(new Request("http://localhost/api/ticker/seasonality?ticker=msft"));
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toContain("no-store");
    const body = await response.json();
    expect(body.source).toBe("sample");
    expect(body.data).toHaveLength(12);
    expectNoUpstreamWork();
  });

  it("does not charge the quota before the UW_TOKEN precondition fails", async () => {
    mocks.requireRouteAccess.mockResolvedValue({ ok: true, principal: { userId: "op", kind: "operator" } });
    mocks.readFile.mockRejectedValue(new Error("ENOENT"));
    const { GET } = await import("../app/api/ticker/seasonality/route");
    const response = await GET(new Request("http://localhost/api/ticker/seasonality?ticker=MSFT"));
    expect(response.status).toBe(500);
    expect(mocks.enforceDemoAiQuota).not.toHaveBeenCalled();
  });

  it("charges the quota once the token precondition passes", async () => {
    process.env.UW_TOKEN = "test-token";
    mocks.requireRouteAccess.mockResolvedValue({ ok: true, principal: { userId: "op", kind: "operator" } });
    mocks.readFile.mockRejectedValue(new Error("ENOENT"));
    mocks.enforceDemoAiQuota.mockResolvedValue(new Response(null, { status: 429 }));
    const { GET } = await import("../app/api/ticker/seasonality/route");
    const response = await GET(new Request("http://localhost/api/ticker/seasonality?ticker=MSFT"));
    expect(response.status).toBe(429);
    expect(mocks.enforceDemoAiQuota).toHaveBeenCalledWith("ticker/seasonality");
    expect(mocks.fetch).not.toHaveBeenCalled();
  });
});
