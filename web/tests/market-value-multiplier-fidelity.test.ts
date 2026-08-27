// R-285 / R-286 / R-287 (REL-097): one correct market value per position,
// including the structures the shared resolver currently gets wrong.
//
// web/CLAUDE.md: "resolveRealtimeMarketValue() is the only real-time
// market-value walk. Every surface calls it ... none re-walks pos.legs to
// accumulate its own." Three holes in that contract:
//
//  (a) PositionTab's spread branch multiplies the combo quote by
//      getMultiplier(position), which collapses to 1 as soon as ANY leg is
//      stock — so a covered call values its 25 short calls at 1x.
//  (b) computeRtMv returns 0, not null, when every leg was skipped, so the
//      `?? resolveMarketValue` fallback never fires and the position renders
//      $0.00 with a full-loss P&L.
//  (c) getLegMultiplier is a two-way Stock/option split with no futures
//      branch, so an ES leg values at 100x instead of its own 50x.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { resolveRealtimeMarketValue, resolveMarketValue } from "@/lib/positionUtils";
import type { PortfolioPosition, PriceData } from "@/lib/types";

function quote(symbol: string, last: number): PriceData {
  return {
    symbol, last, lastIsCalculated: false, bid: last - 0.01, ask: last + 0.01,
    bidSize: 10, askSize: 10, volume: 100, high: last, low: last, open: last, close: last,
    week52High: null, week52Low: null, avgVolume: null, delta: null, gamma: null,
    theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
  } as unknown as PriceData;
}

/** 2,500 shares @ $170 plus 25 short $180 calls @ $5.00. */
const COVERED_CALL = {
  ticker: "AAPL",
  structure_type: "Covered Call",
  expiry: "20261016",
  contracts: 25,
  entry_cost: 0,
  market_value: null,
  legs: [
    { type: "Stock", direction: "LONG", contracts: 2500, strike: null,
      market_price: 170, market_value: 425000, entry_cost: 400000, avg_cost: 160 },
    { type: "Call", direction: "SHORT", contracts: 25, strike: 180,
      market_price: 5, market_value: 12500, entry_cost: 10000, avg_cost: 400 },
  ],
} as unknown as PortfolioPosition;

describe("(c) a futures leg values at its own multiplier", () => {
  const ES = {
    ticker: "ES",
    structure_type: "Future",
    expiry: "20261218",
    contracts: 1,
    entry_cost: 0,
    market_value: null,
    legs: [
      { type: "Future", direction: "LONG", contracts: 1, strike: null,
        multiplier: "50", market_price: 5800, market_value: 290000,
        entry_cost: 285000, avg_cost: 285000 },
    ],
  } as unknown as PortfolioPosition;

  it("prices one ES at 50x, not 100x", () => {
    const mv = resolveRealtimeMarketValue(ES, { ES: quote("ES", 5800) });
    expect(mv).toBe(290000);
  });
});

describe("(b) a position with no contributing leg falls back", () => {
  function zeroContracts(contracts: number | null) {
    return {
      ticker: "MU",
      structure_type: "Vertical Spread",
      expiry: "20261016",
      contracts: 0,
      entry_cost: 1000,
      market_value: 2500,
      legs: [
        { type: "Call", direction: "LONG", contracts, strike: 100,
          market_price: 5, market_value: 2500, entry_cost: 1000, avg_cost: 400 },
      ],
    } as unknown as PortfolioPosition;
  }

  it("returns null rather than 0 when every leg is skipped (contracts: 0)", () => {
    expect(resolveRealtimeMarketValue(zeroContracts(0), { MU: quote("MU", 100) })).toBeNull();
  });

  it("returns null rather than 0 when contracts is null", () => {
    expect(resolveRealtimeMarketValue(zeroContracts(null), { MU: quote("MU", 100) })).toBeNull();
  });

  it("so the caller's `?? resolveMarketValue` restores the synced value", () => {
    const pos = zeroContracts(0);
    const mv = resolveRealtimeMarketValue(pos, { MU: quote("MU", 100) }) ?? resolveMarketValue(pos);
    expect(mv).toBe(2500);
  });
});

describe("(a) the shared walk prices a covered call per leg", () => {
  it("values 2,500 shares at 1x and 25 short calls at 100x", () => {
    const prices = {
      AAPL: quote("AAPL", 170),
      AAPL_20261016_180_C: quote("AAPL_20261016_180_C", 5),
    };
    // 2500 * 170 * 1  -  25 * 5 * 100  =  425,000 - 12,500 = 412,500
    expect(resolveRealtimeMarketValue(COVERED_CALL, prices)).toBe(412500);
  });
});

describe("(a) PositionTab's spread branch does not compute its own value", () => {
  /** Comments are stripped: one quotes the very call it replaced. */
  function source(rel: string): string {
    const raw = readFileSync(resolve(__dirname, "..", rel), "utf8");
    return raw
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split("\n")
      .filter((line) => !line.trim().startsWith("//") && !line.trim().startsWith("*"))
      .join("\n");
  }

  it("prices the combo-quote branch through the shared resolver", () => {
    const src = source("components/ticker-detail/PositionTab.tsx");
    const from = src.indexOf("if (spreadPriceData?.last != null)");
    expect(from).toBeGreaterThan(-1);
    const branch = src.slice(from, src.indexOf("}", src.indexOf("return", from)));
    // The whole defect was `mv: spreadPriceData.last * units * mult`.
    expect(branch).not.toMatch(/mv:\s*spreadPriceData\.last\s*\*/);
    expect(branch).toMatch(/resolveRealtimeMarketValue/);
  });
});
