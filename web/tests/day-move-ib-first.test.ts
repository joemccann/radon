/**
 * Regression: Day Move / Day P&L ESTIMATED (LIVE) was ~4× too large during RTH.
 *
 * Live 2026-07-28 case (Turso snapshot):
 *   IB account daily_pnl  ≈ -$40,889
 *   sum(ib_daily_pnl)     ≈ -$40,932
 *   UI ESTIMATED (LIVE)   ≈ -$159,354  (client close-based math)
 *
 * Root causes in computeDayMoveBreakdown:
 *   1. Required full last+close quotes before accepting ib_daily_pnl — when
 *      quotes were incomplete or ib_daily_pnl was the only good signal, the
 *      position was either dropped or fell through to prior-close math.
 *   2. Same-day positions used yesterday's option close (meaningless) instead
 *      of entry / IB daily, swinging the aggregate by tens of thousands
 *      (e.g. AAOI RR opened today with IB daily +$42k).
 *
 * Fix: prefer ib_daily_pnl without requiring quotes; same-day falls back to
 * entry-based P&L; stock shorts are sign-correct.
 */

import { describe, expect, test, vi, afterEach } from "vitest";
import { computeDayMoveBreakdown } from "../lib/dayMoveBreakdown";
import type { PriceData } from "../lib/pricesProtocol";
import type { PortfolioData, PortfolioPosition } from "../lib/types";

afterEach(() => {
  vi.useRealTimers();
});

const makePrice = (overrides: Partial<PriceData>): PriceData => ({
  symbol: "TEST",
  last: null,
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
  timestamp: "2026-07-28T16:00:00.000Z",
  ...overrides,
});

function makePortfolio(positions: PortfolioPosition[]): PortfolioData {
  return {
    bankroll: 1_320_860.8,
    peak_value: 1_320_860.8,
    last_sync: "2026-07-28T16:57:00.000Z",
    total_deployed_pct: 0,
    total_deployed_dollars: 0,
    remaining_capacity_pct: 100,
    position_count: positions.length,
    defined_risk_count: positions.length,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
    positions,
    account_summary: {
      net_liquidation: 1_320_860.8,
      daily_pnl: null, // RTH snapshot where reqPnL did not arrive
      unrealized_pnl: -374_950.57,
      realized_pnl: 2_383.93,
      settled_cash: 0,
      maintenance_margin: 0,
      excess_liquidity: 0,
      buying_power: 0,
      dividends: 0,
    },
  } as PortfolioData;
}

describe("computeDayMoveBreakdown — prefer IB daily without quotes", () => {
  test("uses ib_daily_pnl even when the option has no live last/close", () => {
    const pos: PortfolioPosition = {
      id: 1,
      ticker: "SNDK",
      structure: "Long Call $1300.0",
      structure_type: "Long Call",
      risk_profile: "defined",
      expiry: "2026-07-31",
      contracts: 10,
      direction: "LONG",
      entry_cost: 78_000,
      max_risk: 78_000,
      market_value: 15_400,
      ib_daily_pnl: -61_737.1,
      legs: [
        {
          direction: "LONG",
          contracts: 10,
          type: "Call",
          strike: 1300,
          entry_cost: 78_000,
          avg_cost: 780,
          market_price: 15.4,
          market_value: 15_400,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-07-27",
    };

    // Empty prices: pre-fix dropped the position (allLegsValid=false).
    const { rows, total } = computeDayMoveBreakdown(makePortfolio([pos]), {});
    expect(rows).toHaveLength(1);
    expect(rows[0].pnl).toBe(-61_737.1);
    expect(total).toBe(-61_737.1);
  });

  test("sums ib_daily_pnl across mixed book when account daily_pnl is null", () => {
    // Mirrors the 2026-07-28 production book: ESTIMATED path must land near
    // IB (~-$41k), not the close-based ~-$159k shown in the bug screenshot.
    const positions: PortfolioPosition[] = [
      {
        id: 1,
        ticker: "AAOI",
        structure: "Risk Reversal",
        structure_type: "Risk Reversal",
        risk_profile: "undefined",
        expiry: "2026-08-15",
        contracts: 100,
        direction: "LONG",
        entry_cost: 10_000,
        max_risk: null,
        market_value: 52_649,
        ib_daily_pnl: 42_649.35,
        legs: [
          {
            direction: "LONG",
            contracts: 100,
            type: "Call",
            strike: 90,
            entry_cost: 50_000,
            avg_cost: 5,
            market_price: 12.6,
            market_value: 126_000,
          },
          {
            direction: "SHORT",
            contracts: 100,
            type: "Put",
            strike: 80,
            entry_cost: 40_000,
            avg_cost: 4,
            market_price: 9.0,
            market_value: 90_000,
          },
        ],
        kelly_optimal: null,
        target: null,
        stop: null,
        entry_date: "2026-07-28", // same-day
      },
      {
        id: 2,
        ticker: "SNDK",
        structure: "Long Call $1300.0",
        structure_type: "Long Call",
        risk_profile: "defined",
        expiry: "2026-07-31",
        contracts: 10,
        direction: "LONG",
        entry_cost: 78_000,
        max_risk: 78_000,
        market_value: 15_400,
        ib_daily_pnl: -61_737.1,
        legs: [
          {
            direction: "LONG",
            contracts: 10,
            type: "Call",
            strike: 1300,
            entry_cost: 78_000,
            avg_cost: 780,
            market_price: 15.4,
            market_value: 15_400,
          },
        ],
        kelly_optimal: null,
        target: null,
        stop: null,
        entry_date: "2026-07-27",
      },
      {
        id: 3,
        ticker: "EWY",
        structure: "Stock",
        structure_type: "Stock",
        risk_profile: "equity",
        expiry: "N/A",
        contracts: 2500,
        direction: "LONG",
        entry_cost: 380_000,
        max_risk: null,
        market_value: 379_025,
        ib_daily_pnl: -23_975.0,
        legs: [
          {
            direction: "LONG",
            contracts: 2500,
            type: "Stock",
            strike: 0,
            entry_cost: 380_000,
            avg_cost: 152,
            market_price: 151.61,
            market_value: 379_025,
          },
        ],
        kelly_optimal: null,
        target: null,
        stop: null,
        entry_date: "2026-07-23",
      },
    ];

    // Deliberately poisoned closes: if close-math wins, AAOI alone looks like a
    // multi-day wipeout instead of today's +$42k fill-based IB daily.
    const prices: Record<string, PriceData> = {
      AAOI_20260815_90_C: makePrice({
        symbol: "AAOI_20260815_90_C",
        last: 12.6,
        close: 40.0, // stale multi-day close — would invent huge day loss
      }),
      AAOI_20260815_80_P: makePrice({
        symbol: "AAOI_20260815_80_P",
        last: 9.0,
        close: 2.0,
      }),
      SNDK_20260731_1300_C: makePrice({
        symbol: "SNDK_20260731_1300_C",
        last: 15.4,
        close: 75.94,
      }),
      EWY: makePrice({ symbol: "EWY", last: 151.61, close: 161.2 }),
    };

    const { total, rows } = computeDayMoveBreakdown(makePortfolio(positions), prices);

    expect(rows).toHaveLength(3);
    // IB sum: 42649.35 - 61737.1 - 23975 = -43062.75
    expect(total).toBeCloseTo(42_649.35 - 61_737.1 - 23_975.0, 2);
    expect(total).toBeGreaterThan(-50_000);
    expect(total).toBeLessThan(-30_000);

    // Poisoned close-math for AAOI alone would be far more negative than IB:
    // LONG call: (12.6-40)*100*100 = -274_000; SHORT put: -(9-2)*100*100 = -70_000
    // → -344_000 just for AAOI. Must not win.
    const aaoiCloseMath = (12.6 - 40) * 100 * 100 + -1 * (9.0 - 2.0) * 100 * 100;
    expect(aaoiCloseMath).toBeLessThan(-300_000);
    expect(total).not.toBeCloseTo(aaoiCloseMath, 0);
  });

  test("same-day option without ib_daily_pnl uses entry cost, not prior close", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-28T18:00:00Z")); // Tue 14:00 ET

    const pos: PortfolioPosition = {
      id: 9,
      ticker: "META",
      structure: "Long Call $620.0",
      structure_type: "Long Call",
      risk_profile: "defined",
      expiry: "2026-08-15",
      contracts: 20,
      direction: "LONG",
      entry_cost: 50_000, // $25/contract * 20 * 100
      max_risk: 50_000,
      market_value: 44_500, // $22.25
      ib_daily_pnl: null,
      legs: [
        {
          direction: "LONG",
          contracts: 20,
          type: "Call",
          strike: 620,
          entry_cost: 50_000,
          avg_cost: 25,
          market_price: 22.25,
          market_value: 44_500,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-07-28",
    };

    const prices: Record<string, PriceData> = {
      META_20260815_620_C: makePrice({
        symbol: "META_20260815_620_C",
        last: 22.25,
        close: 40.0, // yesterday's close — must not be the day-move baseline
      }),
    };

    const { total, rows } = computeDayMoveBreakdown(makePortfolio([pos]), prices);
    expect(rows).toHaveLength(1);
    // Entry-based: 44500 - 50000 = -5500
    expect(total).toBeCloseTo(-5_500, 0);
    // Close-based would be (22.25 - 40) * 20 * 100 = -35_500
    expect(total).not.toBeCloseTo((22.25 - 40) * 20 * 100, 0);
  });

  test("stock day move is sign-aware for SHORT positions", () => {
    const pos: PortfolioPosition = {
      id: 4,
      ticker: "MSFT",
      structure: "Stock",
      structure_type: "Stock",
      risk_profile: "equity",
      expiry: "N/A",
      contracts: 100,
      direction: "SHORT",
      entry_cost: 50_000,
      max_risk: null,
      market_value: 51_000,
      ib_daily_pnl: null,
      legs: [
        {
          direction: "SHORT",
          contracts: 100,
          type: "Stock",
          strike: 0,
          entry_cost: 50_000,
          avg_cost: 500,
          market_price: 510,
          market_value: 51_000,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-07-01",
    };

    const prices = {
      MSFT: makePrice({ symbol: "MSFT", last: 510, close: 500 }),
    };

    const { total } = computeDayMoveBreakdown(makePortfolio([pos]), prices);
    // Short: price up $10 → loss of $1,000
    expect(total).toBe(-1_000);
  });
});
