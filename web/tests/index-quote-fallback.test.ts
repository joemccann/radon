import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PriceData } from "../lib/pricesProtocol";
import { hasUsableIndexPrice, mergeIndexFallbackPrices } from "../lib/useIndexQuoteFallback";

const mockFetch = vi.fn();

function price(symbol: string, last: number | null): PriceData {
  return {
    symbol,
    last,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    fwd: null,
    fwdCurve: null,
    timestamp: "2026-06-23T14:30:00.000Z",
  };
}

function yahooPayload() {
  return {
    chart: {
      result: [{
        meta: {
          symbol: "^VXN",
          regularMarketPrice: 32.29,
          previousClose: 26.31,
          regularMarketTime: 1782239566,
          regularMarketDayHigh: 32.48,
          regularMarketDayLow: 30.56,
          regularMarketOpen: 30.91,
          fiftyTwoWeekHigh: 34.37,
          fiftyTwoWeekLow: 16.49,
        },
        timestamp: [1782221400, 1782239566],
        indicators: {
          quote: [{
            close: [31.7, 32.29],
            high: [32.0, 32.48],
            low: [30.9, 30.56],
            open: [31.0, 30.91],
          }],
        },
      }],
    },
  };
}

beforeEach(() => {
  mockFetch.mockReset();
  vi.stubGlobal("fetch", mockFetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GET /api/index-quote", () => {
  it("returns a Yahoo-backed PriceData fallback for VXN", async () => {
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify(yahooPayload()), { status: 200 }));
    const { GET } = await import("../app/api/index-quote/route");

    const res = await GET(new Request("http://localhost/api/index-quote?symbol=VXN"));
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.source).toBe("yahoo");
    expect(body.price).toMatchObject({
      symbol: "VXN",
      last: 32.29,
      close: 26.31,
      high: 32.48,
      low: 30.56,
      open: 30.91,
      week52High: 34.37,
      week52Low: 16.49,
    });
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("%5EVXN"),
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("rejects non-index symbols", async () => {
    const { GET } = await import("../app/api/index-quote/route");
    const res = await GET(new Request("http://localhost/api/index-quote?symbol=AAPL"));
    expect(res.status).toBe(400);
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

describe("index quote fallback merge", () => {
  it("fills a missing index quote from fallback data", () => {
    const fallback = price("VXN", 32.29);
    expect(mergeIndexFallbackPrices({}, { VXN: fallback }).VXN).toBe(fallback);
  });

  it("does not replace a usable live IB quote", () => {
    const live = price("VXN", 32.01);
    const fallback = price("VXN", 32.29);
    expect(mergeIndexFallbackPrices({ VXN: live }, { VXN: fallback }).VXN).toBe(live);
  });

  it("treats a hollow initial websocket state as unusable", () => {
    expect(hasUsableIndexPrice(price("VXN", null))).toBe(false);
    expect(hasUsableIndexPrice(price("VXN", 32.29))).toBe(true);
  });
});
