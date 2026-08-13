/**
 * @vitest-environment node
 *
 * /api/yield-curve/live — intraday 10Y-3M spread ESTIMATE for the CURVE tab.
 *
 * Sources (established 2026-08-03 by live probe, docs/indicators/curve.md):
 * IB has no live CMT legs (CME micro yield futures 2YY/10Y are delisted;
 * no CBOE index subscription for TNX), UW has no rates endpoints, and Yahoo
 * has no live 2Y — so the live estimate is the 10Y-3M spread from Yahoo
 * ^TNX minus ^IRX (same source, same delay, no modeling). GET-only, 60s
 * in-process cache, 200 + missing:true on any upstream failure.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

function yahooChartBody(symbol: string, price: number, epochSeconds: number) {
  return JSON.stringify({
    chart: {
      result: [
        {
          meta: {
            symbol,
            regularMarketPrice: price,
            regularMarketTime: epochSeconds,
          },
        },
      ],
      error: null,
    },
  });
}

const NOW_S = Math.floor(Date.now() / 1000);

function mockYahoo(
  prices: Partial<Record<string, { price: number; time?: number } | null>>,
) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    for (const [symbol, quote] of Object.entries(prices)) {
      if (url.includes(encodeURIComponent(symbol))) {
        if (quote === null) return new Response("upstream down", { status: 502 });
        return new Response(
          yahooChartBody(symbol, quote!.price, quote!.time ?? NOW_S),
          { status: 200 },
        );
      }
    }
    return new Response("not found", { status: 404 });
  });
}

beforeEach(() => {
  vi.resetModules();
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("GET /api/yield-curve/live", () => {
  it("computes the 10Y-3M spread from ^TNX and ^IRX with an asof timestamp", async () => {
    const fetchMock = mockYahoo({
      "^TNX": { price: 4.686, time: NOW_S },
      "^IRX": { price: 3.7, time: NOW_S - 60 },
    });
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("../app/api/yield-curve/live/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = (await res.json()) as Record<string, unknown>;
    expect(json.missing).toBeUndefined();
    expect(json.y10).toBeCloseTo(4.686, 6);
    expect(json.y3m).toBeCloseTo(3.7, 6);
    expect(json.spread_10y_3m).toBeCloseTo(0.986, 6);
    expect(json.source).toBe("yahoo");
    // asof = the OLDER of the two legs (the estimate is only as fresh as
    // its stalest input).
    expect(json.asof).toBe(new Date((NOW_S - 60) * 1000).toISOString());
  });

  it("returns 200 + missing:true when either leg fails", async () => {
    vi.stubGlobal(
      "fetch",
      mockYahoo({ "^TNX": { price: 4.686 }, "^IRX": null }),
    );

    const { GET } = await import("../app/api/yield-curve/live/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      missing: true,
      y10: null,
      y3m: null,
      spread_10y_3m: null,
      asof: null,
      source: null,
    });
  });

  it("rejects out-of-range yields instead of serving garbage", async () => {
    // A Yahoo scale change (^TNX historically quoted 10x) must read as
    // missing, never as a 46.9% yield.
    vi.stubGlobal(
      "fetch",
      mockYahoo({ "^TNX": { price: 46.86 }, "^IRX": { price: 3.7 } }),
    );

    const { GET } = await import("../app/api/yield-curve/live/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = (await res.json()) as Record<string, unknown>;
    expect(json.missing).toBe(true);
  });

  it("serves from the in-process cache within the TTL (no refetch)", async () => {
    const fetchMock = mockYahoo({
      "^TNX": { price: 4.686 },
      "^IRX": { price: 3.7 },
    });
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("../app/api/yield-curve/live/route");
    await GET();
    const callsAfterFirst = fetchMock.mock.calls.length;
    await GET();
    expect(fetchMock.mock.calls.length).toBe(callsAfterFirst);
  });

  it("coalesces concurrent cache misses into one two-leg refresh", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      await gate;
      const symbol = String(input).includes(encodeURIComponent("^TNX")) ? "^TNX" : "^IRX";
      return new Response(yahooChartBody(symbol, symbol === "^TNX" ? 4.686 : 3.7, NOW_S));
    });
    vi.stubGlobal("fetch", fetchMock);

    const { GET } = await import("../app/api/yield-curve/live/route");
    const first = GET();
    const second = GET();
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    release();
    const responses = await Promise.all([first, second]);
    expect((await responses[0].json()).spread_10y_3m).toBeCloseTo(0.986, 6);
    expect((await responses[1].json()).spread_10y_3m).toBeCloseTo(0.986, 6);
  });

  it("declares force-dynamic", async () => {
    vi.stubGlobal(
      "fetch",
      mockYahoo({ "^TNX": { price: 4.686 }, "^IRX": { price: 3.7 } }),
    );
    const route = await import("../app/api/yield-curve/live/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
