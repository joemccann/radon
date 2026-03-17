/**
 * Unit tests: Today P&L rendering for option positions.
 *
 * Regression target:
 *   When WS option prices lack `close` (after hours / IB delayed-frozen),
 *   Today P&L should return null (not $0). When WS has both `last` and
 *   `close`, Today P&L should compute correctly. When WS has no `last`,
 *   fall back to synced `market_price`.
 */

import { describe, it, expect } from "vitest";

/* ─── Inline replicas of PositionTable logic ─────────────────── */

type Leg = {
  direction: "LONG" | "SHORT";
  contracts: number;
  type: string;
  strike: number | null;
  market_price: number | null;
  market_value: number | null;
};

type PriceData = {
  last: number | null;
  close: number | null;
  bid: number | null;
  ask: number | null;
};

/**
 * Replica of the updated optionsRt logic from PositionTable.tsx.
 */
function computeOptionsRt(
  legs: Leg[],
  prices: Record<string, PriceData | undefined>,
  legKeys: (string | null)[],
) {
  let rtMv = 0;
  let rtDailyPnl = 0;
  let rtCloseValue = 0;
  let hasCloseData = false;

  for (let i = 0; i < legs.length; i++) {
    const leg = legs[i];
    const key = legKeys[i];
    const lp = key ? prices[key] : undefined;
    const last =
      lp?.last != null && lp.last > 0
        ? lp.last
        : leg.market_price != null && leg.market_price > 0
          ? leg.market_price
          : null;
    if (last == null) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    rtMv += sign * last * leg.contracts * 100;
    const close = lp?.close;
    if (close != null && close > 0) {
      rtDailyPnl += sign * (last - close) * leg.contracts * 100;
      rtCloseValue += sign * close * leg.contracts * 100;
      hasCloseData = true;
    }
  }
  return { mv: rtMv, dailyPnl: hasCloseData ? rtDailyPnl : null, closeValue: rtCloseValue };
}

/* ─── Tests ──────────────────────────────────────────────────── */

describe("Today P&L — option position fallback", () => {
  const legs: Leg[] = [
    { direction: "LONG", contracts: 10, type: "Call", strike: 100, market_price: 5.0, market_value: 5000 },
    { direction: "SHORT", contracts: 10, type: "Call", strike: 120, market_price: 2.0, market_value: 2000 },
  ];
  const keys = ["SYM_20260320_100_C", "SYM_20260320_120_C"];

  it("returns correct dailyPnl when WS has last AND close", () => {
    const prices: Record<string, PriceData> = {
      SYM_20260320_100_C: { last: 6.0, close: 5.5, bid: 5.8, ask: 6.2 },
      SYM_20260320_120_C: { last: 2.5, close: 2.2, bid: 2.3, ask: 2.7 },
    };
    const result = computeOptionsRt(legs, prices, keys);
    expect(result).not.toBeNull();
    // LONG leg daily: (6.0 - 5.5) * 10 * 100 = 500
    // SHORT leg daily: -1 * (2.5 - 2.2) * 10 * 100 = -300
    // Net daily P&L = 200
    expect(Math.abs(result!.dailyPnl! - 200)).toBeLessThan(0.01);
    expect(result!.closeValue).not.toBe(0);
  });

  it("returns null dailyPnl when WS has last but NO close (after hours)", () => {
    const prices: Record<string, PriceData> = {
      SYM_20260320_100_C: { last: 6.0, close: null, bid: 5.8, ask: 6.2 },
      SYM_20260320_120_C: { last: 2.5, close: null, bid: 2.3, ask: 2.7 },
    };
    const result = computeOptionsRt(legs, prices, keys);
    expect(result).not.toBeNull();
    // MV should still compute
    expect(result!.mv).toBe((6.0 * 10 * 100) - (2.5 * 10 * 100)); // 3500
    // dailyPnl should be null (no close data), NOT 0
    expect(result!.dailyPnl).toBeNull();
  });

  it("falls back to synced market_price when WS has no last", () => {
    const prices: Record<string, PriceData> = {
      SYM_20260320_100_C: { last: null, close: null, bid: null, ask: null },
      SYM_20260320_120_C: { last: null, close: null, bid: null, ask: null },
    };
    const result = computeOptionsRt(legs, prices, keys);
    expect(result).not.toBeNull();
    // Should use market_price from legs: LONG 5.0, SHORT 2.0
    expect(result!.mv).toBe((5.0 * 10 * 100) - (2.0 * 10 * 100)); // 3000
    expect(result!.dailyPnl).toBeNull(); // no close data
  });

  it("returns null when WS has no data AND synced market_price is null", () => {
    const noSyncLegs: Leg[] = [
      { direction: "LONG", contracts: 10, type: "Call", strike: 100, market_price: null, market_value: null },
    ];
    const prices: Record<string, PriceData> = {
      SYM_20260320_100_C: { last: null, close: null, bid: null, ask: null },
    };
    const result = computeOptionsRt(noSyncLegs, prices, ["SYM_20260320_100_C"]);
    expect(result).toBeNull();
  });

  it("uses cached close when available with WS last", () => {
    const prices: Record<string, PriceData> = {
      SYM_20260320_100_C: { last: 6.0, close: 5.5, bid: 5.8, ask: 6.2 },
      SYM_20260320_120_C: { last: 2.5, close: 2.2, bid: 2.3, ask: 2.7 },
    };
    const result = computeOptionsRt(legs, prices, keys);
    expect(result).not.toBeNull();
    expect(result!.dailyPnl).not.toBeNull();
    expect(Math.abs(result!.dailyPnl! - 200)).toBeLessThan(0.01);
  });

  it("works with mixed WS + synced data (partial WS coverage)", () => {
    const prices: Record<string, PriceData> = {
      SYM_20260320_100_C: { last: 6.0, close: 5.5, bid: 5.8, ask: 6.2 },
      // Second leg has no WS data — falls back to market_price
      SYM_20260320_120_C: { last: null, close: null, bid: null, ask: null },
    };
    const result = computeOptionsRt(legs, prices, keys);
    expect(result).not.toBeNull();
    // LONG leg uses WS last=6.0, SHORT uses synced market_price=2.0
    expect(result!.mv).toBe((6.0 * 10 * 100) - (2.0 * 10 * 100)); // 4000
    // Only LONG leg has close data, so dailyPnl reflects only that leg
    expect(result!.dailyPnl).toBe((6.0 - 5.5) * 10 * 100); // 500
  });
});
