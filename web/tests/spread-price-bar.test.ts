import { describe, it, expect } from "vitest";
import { resolveSpreadPriceData } from "@/lib/positionUtils";
import type { PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

function makePriceData(overrides: Partial<PriceData> & { symbol: string }): PriceData {
  return {
    last: null, lastIsCalculated: false,
    bid: null, ask: null, bidSize: null, askSize: null,
    volume: null, high: null, low: null, open: null, close: null,
    week52High: null, week52Low: null, avgVolume: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

const bullCallSpread: PortfolioPosition = {
  id: 1,
  ticker: "GOOG",
  direction: "LONG",
  structure: "DEBIT 44X BULL CALL SPREAD $315.0/$340.0",
  structure_type: "Vertical",
  expiry: "2026-03-20",
  contracts: 44,
  entry_cost: 22880,
  market_value: 19800,
  market_price: 4.5,
  market_price_is_calculated: false,
  pnl: -3080,
  pnl_pct: -13.46,
  legs: [
    { type: "Call", strike: 315, direction: "LONG", contracts: 44, entry_cost: 56760, market_value: 37400, market_price_is_calculated: false },
    { type: "Call", strike: 340, direction: "SHORT", contracts: 44, entry_cost: 33880, market_value: 17600, market_price_is_calculated: false },
  ],
};

describe("resolveSpreadPriceData", () => {
  it("computes net bid/ask/last for a bull call spread from per-leg WS prices", () => {
    const prices: Record<string, PriceData> = {
      "GOOG_20260320_315_C": makePriceData({ symbol: "GOOG", bid: 8.50, ask: 8.80, last: 8.65 }),
      "GOOG_20260320_340_C": makePriceData({ symbol: "GOOG", bid: 4.00, ask: 4.20, last: 4.10 }),
    };

    const result = resolveSpreadPriceData("GOOG", bullCallSpread, prices);
    expect(result).not.toBeNull();
    // Net bid = Σ(sign × leg.bid) = 8.50 - 4.00 = 4.50
    // Net ask = Σ(sign × leg.ask) = 8.80 - 4.20 = 4.60
    // lo = min(4.50, 4.60) = 4.50, hi = max(4.50, 4.60) = 4.60
    // Net last = long last - short last = 8.65 - 4.10 = 4.55
    expect(result!.bid).toBeCloseTo(4.50, 2);
    expect(result!.ask).toBeCloseTo(4.60, 2);
    expect(result!.last).toBeCloseTo(4.55, 2);
    expect(result!.symbol).toBe("GOOG");
  });

  it("returns null when per-leg prices are missing", () => {
    const prices: Record<string, PriceData> = {
      "GOOG_20260320_315_C": makePriceData({ symbol: "GOOG", bid: 8.50, ask: 8.80 }),
      // Missing short leg
    };
    const result = resolveSpreadPriceData("GOOG", bullCallSpread, prices);
    expect(result).toBeNull();
  });

  it("returns null when bid/ask are null on a leg", () => {
    const prices: Record<string, PriceData> = {
      "GOOG_20260320_315_C": makePriceData({ symbol: "GOOG", bid: 8.50, ask: 8.80 }),
      "GOOG_20260320_340_C": makePriceData({ symbol: "GOOG", bid: null, ask: null }),
    };
    const result = resolveSpreadPriceData("GOOG", bullCallSpread, prices);
    expect(result).toBeNull();
  });

  it("uses mid when last is null on a leg", () => {
    const prices: Record<string, PriceData> = {
      "GOOG_20260320_315_C": makePriceData({ symbol: "GOOG", bid: 8.50, ask: 8.80, last: null }),
      "GOOG_20260320_340_C": makePriceData({ symbol: "GOOG", bid: 4.00, ask: 4.20, last: 4.10 }),
    };
    const result = resolveSpreadPriceData("GOOG", bullCallSpread, prices);
    expect(result).not.toBeNull();
    // Long leg last falls back to mid = (8.50 + 8.80) / 2 = 8.65
    // Net last = 8.65 - 4.10 = 4.55
    expect(result!.last).toBeCloseTo(4.55, 2);
  });

  it("uses guarded leg marks when stale option lasts sit far outside the live market", () => {
    const prices: Record<string, PriceData> = {
      "GOOG_20260320_315_C": makePriceData({ symbol: "GOOG_20260320_315_C", bid: 1.85, ask: 2.00, last: 7.80 }),
      "GOOG_20260320_340_C": makePriceData({ symbol: "GOOG_20260320_340_C", bid: 0.22, ask: 0.33, last: 2.55 }),
    };

    const result = resolveSpreadPriceData("GOOG", bullCallSpread, prices);
    expect(result).not.toBeNull();
    // Long leg resolves to mid 1.925, short leg resolves to mid 0.275.
    // Net last should reflect the live spread mark, not the stale 7.80 - 2.55 print.
    expect(result!.last).toBeCloseTo(1.65, 2);
  });

  it("returns null for single-leg positions", () => {
    const singleLeg: PortfolioPosition = {
      ...bullCallSpread,
      legs: [bullCallSpread.legs[0]],
    };
    const prices: Record<string, PriceData> = {};
    const result = resolveSpreadPriceData("GOOG", singleLeg, prices);
    expect(result).toBeNull();
  });

  it("returns null for stock positions", () => {
    const stock: PortfolioPosition = {
      ...bullCallSpread,
      structure_type: "Stock",
      legs: [],
    };
    const result = resolveSpreadPriceData("GOOG", stock, {});
    expect(result).toBeNull();
  });

  it("ensures bid <= ask in net result (debit spread)", () => {
    const prices: Record<string, PriceData> = {
      "GOOG_20260320_315_C": makePriceData({ symbol: "GOOG", bid: 8.50, ask: 8.80, last: 8.65 }),
      "GOOG_20260320_340_C": makePriceData({ symbol: "GOOG", bid: 4.00, ask: 4.20, last: 4.10 }),
    };
    const result = resolveSpreadPriceData("GOOG", bullCallSpread, prices);
    expect(result!.bid).toBeLessThanOrEqual(result!.ask!);
  });

  it("handles bear put spread (long higher strike, short lower)", () => {
    const bearPut: PortfolioPosition = {
      ...bullCallSpread,
      structure: "DEBIT 10X BEAR PUT SPREAD $340.0/$315.0",
      legs: [
        { type: "Put", strike: 340, direction: "LONG", contracts: 10, entry_cost: 20000, market_value: 18000, market_price_is_calculated: false },
        { type: "Put", strike: 315, direction: "SHORT", contracts: 10, entry_cost: 8000, market_value: 6000, market_price_is_calculated: false },
      ],
    };
    const prices: Record<string, PriceData> = {
      "GOOG_20260320_340_P": makePriceData({ symbol: "GOOG", bid: 18.00, ask: 18.50, last: 18.25 }),
      "GOOG_20260320_315_P": makePriceData({ symbol: "GOOG", bid: 6.00, ask: 6.30, last: 6.15 }),
    };
    const result = resolveSpreadPriceData("GOOG", bearPut, prices);
    expect(result).not.toBeNull();
    // Net last = 18.25 - 6.15 = 12.10
    expect(result!.last).toBeCloseTo(12.10, 2);
    expect(result!.bid).toBeLessThanOrEqual(result!.ask!);
  });
});
