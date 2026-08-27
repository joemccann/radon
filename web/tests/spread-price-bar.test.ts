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
  it("uses the live net midpoint as the synthetic combo mark when leg last trades are stale", () => {
    const riskReversal: PortfolioPosition = {
      ...bullCallSpread,
      ticker: "IWM",
      structure: "Risk Reversal (P$243.0/C$247.0)",
      structure_type: "Risk Reversal",
      direction: "COMBO",
      contracts: 50,
      entry_cost: -579.79,
      market_value: 1800,
      legs: [
        { type: "Call", strike: 247, direction: "LONG", contracts: 50, entry_cost: 17285.02, market_value: 17300, market_price_is_calculated: false },
        { type: "Put", strike: 243, direction: "SHORT", contracts: 50, entry_cost: 17864.81, market_value: 16000, market_price_is_calculated: false },
      ],
    };

    const prices: Record<string, PriceData> = {
      "IWM_20260320_247_C": makePriceData({ symbol: "IWM_20260320_247_C", bid: 3.25, ask: 3.28, last: 3.26 }),
      "IWM_20260320_243_P": makePriceData({ symbol: "IWM_20260320_243_P", bid: 3.00, ask: 3.02, last: 3.51 }),
    };

    const result = resolveSpreadPriceData("IWM", riskReversal, prices);
    expect(result).not.toBeNull();
    expect(result!.bid).toBeCloseTo(0.23, 2);
    expect(result!.ask).toBeCloseTo(0.28, 2);
    expect(result!.last).toBeCloseTo(0.26, 2);
    expect(result!.lastIsCalculated).toBe(true);
  });

  it("computes net bid/ask/last for a bull call spread from per-leg WS prices", () => {
    const prices: Record<string, PriceData> = {
      "GOOG_20260320_315_C": makePriceData({ symbol: "GOOG", bid: 8.50, ask: 8.80, last: 8.65 }),
      "GOOG_20260320_340_C": makePriceData({ symbol: "GOOG", bid: 4.00, ask: 4.20, last: 4.10 }),
    };

    const result = resolveSpreadPriceData("GOOG", bullCallSpread, prices);
    expect(result).not.toBeNull();
    // Natural bid sells the long at 8.50 and buys back the short at 4.20.
    // Natural ask buys the long at 8.80 and sells the short at 4.00.
    expect(result!.bid).toBeCloseTo(4.30, 2);
    expect(result!.ask).toBeCloseTo(4.80, 2);
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

  it("prices calendars from each leg's expiry instead of collapsing both contracts", () => {
    const calendar: PortfolioPosition = {
      ...bullCallSpread,
      structure: "Calendar Call Spread",
      structure_type: "Calendar Call Spread",
      legs: [
        { ...bullCallSpread.legs[0], strike: 320, expiry: "2026-06-19", direction: "LONG" },
        { ...bullCallSpread.legs[1], strike: 320, expiry: "2026-03-20", direction: "SHORT" },
      ],
    };
    const prices: Record<string, PriceData> = {
      "GOOG_20260619_320_C": makePriceData({ symbol: "GOOG_20260619_320_C", bid: 8, ask: 8.2 }),
      "GOOG_20260320_320_C": makePriceData({ symbol: "GOOG_20260320_320_C", bid: 3, ask: 3.1 }),
    };
    const result = resolveSpreadPriceData("GOOG", calendar, prices);
    expect(result?.bid).toBeCloseTo(4.9, 2);
    expect(result?.ask).toBeCloseTo(5.2, 2);
  });

  /* ── Freshness: T-217, the same defect class T-158 closed in
       `comboQuotePriceData`. The spread header's BID/MID/ASK is only as fresh
       as its stalest leg. Stamping the wall clock makes an hours-old quote
       render as live. Window-relative: the leg ages are offsets from a fixed
       base, never a hardcoded date. ── */
  describe("freshness carries the stalest leg, not the wall clock", () => {
    const BASE_MS = Date.parse("2026-03-02T15:30:00.000Z");
    const iso = (minutesAgo: number) => new Date(BASE_MS - minutesAgo * 60_000).toISOString();

    it("stamps the oldest leg quote timestamp, not now", () => {
      const stalest = iso(20);
      const prices: Record<string, PriceData> = {
        "GOOG_20260320_315_C": makePriceData({ symbol: "GOOG", bid: 8.50, ask: 8.80, timestamp: iso(12) }),
        "GOOG_20260320_340_C": makePriceData({ symbol: "GOOG", bid: 4.00, ask: 4.20, timestamp: stalest }),
      };

      const result = resolveSpreadPriceData("GOOG", bullCallSpread, prices);
      expect(result).not.toBeNull();
      expect(result!.timestamp).toBe(stalest);
    });

    it("fails closed with an unparseable stamp when a leg has no usable quote age", () => {
      const prices: Record<string, PriceData> = {
        "GOOG_20260320_315_C": makePriceData({ symbol: "GOOG", bid: 8.50, ask: 8.80, timestamp: iso(3) }),
        "GOOG_20260320_340_C": makePriceData({ symbol: "GOOG", bid: 4.00, ask: 4.20, timestamp: "" }),
      };

      const result = resolveSpreadPriceData("GOOG", bullCallSpread, prices);
      expect(result).not.toBeNull();
      // Unknown leg age is not "fresh". An empty stamp parses to NaN so the
      // header relabels to CLOSE rather than borrowing the wall clock.
      expect(result!.timestamp).toBe("");
      expect(Number.isNaN(Date.parse(result!.timestamp))).toBe(true);
    });
  });
});
