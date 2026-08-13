/**
 * Unit tests: Exposure breakdown — per-leg delta signs.
 *
 * Expanded leg rows must show signed position exposure:
 * LONG Call +, SHORT Call -, LONG Put -, SHORT Put +.
 */

import { describe, it, expect } from "vitest";
import { computeExposureDetailed } from "@/lib/exposureBreakdown";
import type { PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

function makePriceData(overrides: Partial<PriceData> = {}): PriceData {
  return { last: null, bid: null, ask: null, close: null, volume: null, ...overrides };
}

function futureExpiry(days = 180): string {
  return new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);
}

const SPREAD_POSITION: PortfolioData = {
  bankroll: 100_000,
  positions: [
    {
      id: 1,
      ticker: "AAPL",
      structure: "Bull Call Spread $270.0/$290.0",
      structure_type: "Vertical",
      risk_profile: "defined",
      direction: "DEBIT",
      contracts: 100,
      expiry: "2026-04-17",
      market_value: 41_700,
      legs: [
        { type: "Call", direction: "LONG", strike: 270, contracts: 100, avg_cost: 901, market_value: 63_000 },
        { type: "Call", direction: "SHORT", strike: 290, contracts: 100, avg_cost: -500, market_value: -21_300 },
      ],
    },
  ],
  account_summary: {},
  exposure: {},
  violations: [],
};

describe("Exposure breakdown — short leg delta sign", () => {
  it("short leg rawDelta is negative when IB provides delta", () => {
    const prices: Record<string, PriceData> = {
      AAPL: makePriceData({ last: 260 }),
      // IB deltas: long call delta = 0.36, short call delta = 0.08
      // Key format: TICKER_YYYYMMDD_STRIKE_RIGHT
      "AAPL_20260417_270_C": makePriceData({ last: 6.30, delta: 0.36 }),
      "AAPL_20260417_290_C": makePriceData({ last: 2.13, delta: 0.08 }),
    };

    const result = computeExposureDetailed(SPREAD_POSITION, prices);
    const row = result.rows[0];
    expect(row.legs).toHaveLength(2);

    // Long leg: rawDelta should be positive
    const longLeg = row.legs.find((l) => l.direction === "LONG")!;
    expect(longLeg.rawDelta).toBeCloseTo(0.36, 4);
    expect(longLeg.legDelta).toBeCloseTo(3600, 0);

    // Short leg: rawDelta must be NEGATIVE
    const shortLeg = row.legs.find((l) => l.direction === "SHORT")!;
    expect(shortLeg.rawDelta).toBeCloseTo(-0.08, 4);
    expect(shortLeg.legDelta).toBeCloseTo(-800, 0);

    // Position-level delta: net of long - short
    expect(row.delta).toBeCloseTo(2800, 0);
  });

  it("normalizes positive IB put deltas before applying leg direction", () => {
    const portfolio: PortfolioData = {
      bankroll: 1_500_000,
      positions: [
        {
          id: 4,
          ticker: "SPY",
          structure: "Bear Put Spread $650.0/$740.0",
          structure_type: "Bear Put Spread",
          risk_profile: "defined",
          direction: "DEBIT",
          contracts: 50,
          expiry: "2026-09-18",
          market_value: 64_750,
          legs: [
            { type: "Put", direction: "SHORT", strike: 650, contracts: 50, avg_cost: -1026.27, market_value: 26_600 },
            { type: "Put", direction: "LONG", strike: 740, contracts: 50, avg_cost: 3250.7, market_value: 91_350 },
          ],
        },
      ],
      account_summary: {},
      exposure: {},
      violations: [],
    };

    const prices: Record<string, PriceData> = {
      SPY: makePriceData({ last: 737.46 }),
      "SPY_20260918_650_P": makePriceData({ last: 6.68, delta: 0.2468 }),
      "SPY_20260918_740_P": makePriceData({ last: 24.45, delta: 0.1623 }),
    };

    const result = computeExposureDetailed(portfolio, prices);
    const row = result.rows[0];
    const shortPut = row.legs.find((l) => l.direction === "SHORT")!;
    const longPut = row.legs.find((l) => l.direction === "LONG")!;

    expect(row.deltaSource).toBe("ib");
    expect(shortPut.rawDelta).toBeCloseTo(0.7532, 4);
    expect(shortPut.legDelta).toBeCloseTo(3766, 0);
    expect(longPut.rawDelta).toBeCloseTo(-0.8377, 4);
    expect(longPut.legDelta).toBeCloseTo(-4188.5, 4);
    expect(row.delta).toBeCloseTo(-422.5, 4);
    expect(row.dollarDelta).toBeLessThan(0);
  });

  it("short leg rawDelta is negative with approx delta fallback", () => {
    const prices: Record<string, PriceData> = {
      AAPL: makePriceData({ last: 260 }),
      // No IB delta — will use approx
    };

    const result = computeExposureDetailed(SPREAD_POSITION, prices);
    const row = result.rows[0];

    const shortLeg = row.legs.find((l) => l.direction === "SHORT")!;
    // Short call rawDelta must be negative
    expect(shortLeg.rawDelta).toBeLessThan(0);
    expect(shortLeg.legDelta).toBeLessThan(0);

    const longLeg = row.legs.find((l) => l.direction === "LONG")!;
    expect(longLeg.rawDelta).toBeGreaterThan(0);
    expect(longLeg.legDelta).toBeGreaterThan(0);
  });

  it("labels a position mixed when any leg uses approximation alongside IB delta", () => {
    const prices: Record<string, PriceData> = {
      AAPL: makePriceData({ last: 260 }),
      "AAPL_20260417_270_C": makePriceData({ last: 6.3, delta: 0.36 }),
    };

    const row = computeExposureDetailed(SPREAD_POSITION, prices).rows[0];
    expect(row.deltaSource).toBe("mixed");
  });

  it("stock leg rawDelta reflects direction sign", () => {
    const portfolio: PortfolioData = {
      bankroll: 100_000,
      positions: [
        {
          id: 2,
          ticker: "MSFT",
          structure: "Stock (1000 shares)",
          structure_type: "Stock",
          risk_profile: "equity",
          direction: "LONG",
          contracts: 1000,
          expiry: "N/A",
          market_value: 400_000,
          legs: [
            { type: "Stock", direction: "LONG", strike: null, contracts: 1000, avg_cost: 468_000, market_value: 400_000 },
          ],
        },
      ],
      account_summary: {},
      exposure: {},
      violations: [],
    };

    const prices: Record<string, PriceData> = {
      MSFT: makePriceData({ last: 400 }),
    };

    const result = computeExposureDetailed(portfolio, prices);
    const stockLeg = result.rows[0].legs[0];
    expect(stockLeg.rawDelta).toBe(1);
    expect(stockLeg.legDelta).toBe(1000);
  });

  it("approximates a bear put spread as net negative delta between strikes", () => {
    const portfolio: PortfolioData = {
      bankroll: 1_500_000,
      positions: [
        {
          id: 3,
          ticker: "SPY",
          structure: "Bear Put Spread $650.0/$740.0",
          structure_type: "Vertical",
          risk_profile: "defined",
          direction: "DEBIT",
          contracts: 50,
          expiry: futureExpiry(),
          market_value: 200_000,
          legs: [
            { type: "Put", direction: "SHORT", strike: 650, contracts: 50, avg_cost: -250, market_value: -12_500 },
            { type: "Put", direction: "LONG", strike: 740, contracts: 50, avg_cost: 600, market_value: 30_000 },
          ],
        },
      ],
      account_summary: {},
      exposure: {},
      violations: [],
    };

    const result = computeExposureDetailed(portfolio, {
      SPY: makePriceData({ last: 734.97 }),
    });
    const row = result.rows[0];
    const shortPut = row.legs.find((l) => l.direction === "SHORT")!;
    const longPut = row.legs.find((l) => l.direction === "LONG")!;

    expect(shortPut.rawDelta).toBeGreaterThan(0);
    expect(longPut.rawDelta).toBeLessThan(0);
    expect(Math.abs(shortPut.rawDelta ?? 0)).toBeLessThan(Math.abs(longPut.rawDelta ?? 0));
    expect(row.delta).toBeLessThan(0);
    expect(row.dollarDelta).toBeLessThan(0);
    expect(result.dollarDelta).toBeLessThan(0);
  });
});
