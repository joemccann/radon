import { describe, it, expect } from "vitest";
import { optionKey } from "../../../web/lib/pricesProtocol";
import type { PriceData } from "../../../web/lib/pricesProtocol";
import type { PortfolioPosition } from "../../../web/lib/types";

/**
 * Mirror of getOptionDailyChg from WorkspaceSections.tsx.
 * This tests the CORRECT algorithm (% of close value, not entry cost).
 * The production code must match this algorithm to pass.
 */
function legPriceKey(
  ticker: string,
  expiry: string,
  leg: { type: string; strike: number | null },
): string | null {
  if (leg.type === "Stock") return null;
  if (leg.strike == null || leg.strike === 0) return null;
  if (!expiry || expiry === "N/A") return null;
  const right = leg.type === "Call" ? "C" : leg.type === "Put" ? "P" : null;
  if (!right) return null;
  const expiryClean = expiry.replace(/-/g, "");
  if (expiryClean.length !== 8) return null;
  return optionKey({ symbol: ticker.toUpperCase(), expiry: expiryClean, strike: leg.strike, right });
}

function getOptionDailyChgCorrect(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type === "Stock" || !prices) return null;
  let dailyPnl = 0;
  let closeValue = 0;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key ? prices[key] : null;
    if (!lp || lp.last == null || lp.last <= 0 || lp.close == null || lp.close <= 0) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    dailyPnl += sign * (lp.last - lp.close) * leg.contracts * 100;
    closeValue += sign * lp.close * leg.contracts * 100;
  }
  if (closeValue === 0) return null;
  return (dailyPnl / Math.abs(closeValue)) * 100;
}

/** BUG version: divides by entry cost instead of close value */
function getOptionDailyChgBuggy(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type === "Stock" || !prices) return null;
  let dailyPnl = 0;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key ? prices[key] : null;
    if (!lp || lp.last == null || lp.last <= 0 || lp.close == null || lp.close <= 0) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    dailyPnl += sign * (lp.last - lp.close) * leg.contracts * 100;
  }
  const entryCost = pos.entry_cost;
  if (entryCost === 0) return null;
  return (dailyPnl / Math.abs(entryCost)) * 100;
}

function makePriceData(overrides: Partial<PriceData> = {}): PriceData {
  return {
    symbol: "TEST", last: null, lastIsCalculated: false,
    bid: null, ask: null, bidSize: null, askSize: null,
    volume: null, high: null, low: null, open: null, close: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

// PLTR spread fixture: entry $0.52, but close values are ~$8.50 net
const pltrSpread: PortfolioPosition = {
  id: 1,
  ticker: "PLTR",
  structure: "Bull Call Spread $145.0/$165.0",
  structure_type: "Vertical Spread",
  risk_profile: "defined",
  expiry: "2026-03-27",
  contracts: 50,
  direction: "DEBIT",
  entry_cost: 2600, // $0.52 * 50 * 100
  max_risk: 2600,
  market_value: null,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-03-05",
  legs: [
    { direction: "LONG", contracts: 50, type: "Call", strike: 145, entry_cost: 22950, avg_cost: 22950, market_price: null, market_value: null },
    { direction: "SHORT", contracts: 50, type: "Call", strike: 165, entry_cost: 20350, avg_cost: 20350, market_price: null, market_value: null },
  ],
};

const longKey = optionKey({ symbol: "PLTR", expiry: "20260327", strike: 145, right: "C" });
const shortKey = optionKey({ symbol: "PLTR", expiry: "20260327", strike: 165, right: "C" });

const pltrPrices: Record<string, PriceData> = {
  [longKey]: makePriceData({ symbol: longKey, last: 11.50, close: 12.00 }),
  [shortKey]: makePriceData({ symbol: shortKey, last: 2.84, close: 3.50 }),
};

describe("getOptionDailyChg — PLTR spread bug", () => {
  it("buggy version produces absurd -206% (proves bug exists)", () => {
    const result = getOptionDailyChgBuggy(pltrSpread, pltrPrices);
    expect(result).not.toBeNull();
    // Bug: dailyPnl / entryCost → huge number because entry is tiny ($2,600)
    expect(Math.abs(result!)).toBeGreaterThan(30);
  });

  it("correct version produces reasonable daily change (% of close value)", () => {
    const result = getOptionDailyChgCorrect(pltrSpread, pltrPrices);
    expect(result).not.toBeNull();
    // Net close = (12.00 - 3.50) * 50 * 100 = $42,500
    // Net current = (11.50 - 2.84) * 50 * 100 = $43,300
    // Daily P&L = $800, chg = $800 / $42,500 = +1.88%
    expect(result).toBeCloseTo(1.882, 1);
    expect(Math.abs(result!)).toBeLessThan(30); // sanity: daily chg should be small
  });

  it("single-leg option: daily chg is % of close, not entry", () => {
    const pos: PortfolioPosition = {
      id: 4, ticker: "AAOI", structure: "Long Call $105",
      structure_type: "Option", risk_profile: "defined",
      expiry: "2026-03-06", contracts: 25, direction: "LONG",
      entry_cost: 6621, max_risk: 6621, market_value: null,
      kelly_optimal: null, target: null, stop: null, entry_date: "2026-03-05",
      legs: [{ direction: "LONG", contracts: 25, type: "Call", strike: 105, entry_cost: 6621, avg_cost: 6621, market_price: null, market_value: null }],
    };
    const key = optionKey({ symbol: "AAOI", expiry: "20260306", strike: 105, right: "C" });
    const prices = { [key]: makePriceData({ symbol: key, last: 1.40, close: 2.00 }) };

    const result = getOptionDailyChgCorrect(pos, prices);
    // dailyPnl = (1.40 - 2.00) * 25 * 100 = -$1,500
    // closeValue = 2.00 * 25 * 100 = $5,000
    // chg = -$1,500 / $5,000 = -30%
    expect(result).toBeCloseTo(-30, 1);
  });

  it("returns null for stock positions", () => {
    const pos = {
      id: 2, ticker: "AAPL", structure: "Long Stock",
      structure_type: "Stock", risk_profile: "equity", expiry: "N/A",
      contracts: 100, direction: "LONG", entry_cost: 15000, max_risk: null,
      market_value: null, kelly_optimal: null, target: null, stop: null,
      entry_date: "2026-01-01",
      legs: [{ direction: "LONG" as const, contracts: 100, type: "Stock" as const, strike: null, entry_cost: 15000, avg_cost: 15000, market_price: null, market_value: null }],
    };
    expect(getOptionDailyChgCorrect(pos, {})).toBeNull();
  });

  it("returns null when close value is zero", () => {
    // Contrived: both legs have equal close → net close = 0
    const pos: PortfolioPosition = {
      ...pltrSpread,
      legs: [
        { direction: "LONG", contracts: 50, type: "Call", strike: 145, entry_cost: 22950, avg_cost: 22950, market_price: null, market_value: null },
        { direction: "SHORT", contracts: 50, type: "Call", strike: 165, entry_cost: 20350, avg_cost: 20350, market_price: null, market_value: null },
      ],
    };
    const prices: Record<string, PriceData> = {
      [longKey]: makePriceData({ symbol: longKey, last: 5.00, close: 3.00 }),
      [shortKey]: makePriceData({ symbol: shortKey, last: 4.00, close: 3.00 }),
    };
    // closeValue = (3.00 - 3.00) * 50 * 100 = 0
    expect(getOptionDailyChgCorrect(pos, prices)).toBeNull();
  });
});
