/**
 * @vitest-environment jsdom
 *
 * Day Move breakdown modal rows for option positions.
 *
 * Live 2026-10-21 case: the modal printed a combo's FIRST leg quote as
 * CLOSE / CURRENT (SPCX bull call spread "$1.80 → $3.10" was the $175 call
 * alone), every option row read "N/A" in the % column, and two rows carried
 * an IB daily P&L whose sign contradicted the quoted move beside it
 * (IWO 1.70 → 2.95 with −$5,081; GOOGL 10.70 → 8.37 with +$800) with no
 * marker that the two sources disagreed.
 *
 * Expected: CLOSE / CURRENT are the net per-unit combo prices (credits
 * negative), % is the row's P&L over the absolute net close value, and a row
 * whose IB daily P&L disagrees in sign with the quoted move is flagged.
 */

import React from "react";
import { afterEach, describe, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { computeDayMoveBreakdown } from "@/lib/dayMoveBreakdown";
import PnlBreakdownModal from "@/components/PnlBreakdownModal";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioData, PortfolioLeg, PortfolioPosition } from "@/lib/types";

afterEach(() => cleanup());

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
  timestamp: "2026-10-21T20:40:00.000Z",
  ...overrides,
});

function makePortfolio(positions: PortfolioPosition[]): PortfolioData {
  return {
    bankroll: 1_210_801.92,
    peak_value: 1_210_801.92,
    last_sync: "2026-10-21T20:40:08.000Z",
    total_deployed_pct: 0,
    total_deployed_dollars: 0,
    remaining_capacity_pct: 100,
    position_count: positions.length,
    defined_risk_count: positions.length,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
    positions,
    account_summary: {
      net_liquidation: 1_210_801.92,
      daily_pnl: null,
      unrealized_pnl: -520_168.44,
      realized_pnl: 0,
      settled_cash: 0,
      maintenance_margin: 0,
      excess_liquidity: 0,
      buying_power: 0,
      dividends: 0,
    },
  } as PortfolioData;
}

function leg(direction: "LONG" | "SHORT", type: "Call" | "Put", strike: number, contracts: number): PortfolioLeg {
  return {
    direction,
    contracts,
    type,
    strike,
    entry_cost: 0,
    avg_cost: 0,
    market_price: null,
    market_value: null,
  };
}

function makeOptionPosition(overrides: Partial<PortfolioPosition> & { legs: PortfolioLeg[] }): PortfolioPosition {
  return {
    id: 1,
    ticker: "SPCX",
    structure: "Bull Call Spread $175.0/$200.0",
    structure_type: "Bull Call Spread",
    risk_profile: "defined",
    expiry: "2026-11-13",
    contracts: 200,
    direction: "LONG",
    entry_cost: 87_800,
    max_risk: 87_800,
    market_value: 49_800,
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-09-01",
    ...overrides,
  } as PortfolioPosition;
}

const spcxLegs = [leg("LONG", "Call", 175, 200), leg("SHORT", "Call", 200, 200)];
const spcxPrices: Record<string, PriceData> = {
  SPCX_20261113_175_C: makePrice({ symbol: "SPCX_20261113_175_C", last: 3.1, close: 1.8 }),
  SPCX_20261113_200_C: makePrice({ symbol: "SPCX_20261113_200_C", last: 1.2, close: 0.5 }),
};

describe("computeDayMoveBreakdown — net combo quotes", () => {
  test("IB-daily combo row shows net per-unit close and current, not the first leg", () => {
    const pos = makeOptionPosition({ legs: spcxLegs, ib_daily_pnl: 25_232.97 });
    const { rows } = computeDayMoveBreakdown(makePortfolio([pos]), spcxPrices);
    expect(rows).toHaveLength(1);
    expect(rows[0].col1).toBe("$1.30");
    expect(rows[0].col2).toBe("$1.90");
  });

  test("IB-daily combo row carries % = P&L over absolute net close value", () => {
    const pos = makeOptionPosition({ legs: spcxLegs, ib_daily_pnl: 25_232.97 });
    const { rows } = computeDayMoveBreakdown(makePortfolio([pos]), spcxPrices);
    // net close value = 1.30 × 200 × 100 = 26,000
    expect(rows[0].pnlPct).toBeCloseTo((25_232.97 / 26_000) * 100, 6);
  });

  test("single short option row prints the credit as a negative price", () => {
    const pos = makeOptionPosition({
      id: 2,
      ticker: "META",
      structure: "Short Put $580.0",
      structure_type: "Short Put",
      direction: "SHORT",
      contracts: 40,
      expiry: "2026-11-20",
      legs: [leg("SHORT", "Put", 580, 40)],
      ib_daily_pnl: 1_200,
    });
    const prices = {
      META_20261120_580_P: makePrice({ symbol: "META_20261120_580_P", last: 1.7, close: 2.0 }),
    };
    const { rows } = computeDayMoveBreakdown(makePortfolio([pos]), prices);
    expect(rows[0].col1).toBe("-$2.00");
    expect(rows[0].col2).toBe("-$1.70");
    expect(rows[0].pnlPct).toBeCloseTo((1_200 / 8_000) * 100, 6);
  });

  test("overnight option combo without IB daily nets the legs and carries %", () => {
    const pos = makeOptionPosition({ legs: spcxLegs });
    const { rows, total } = computeDayMoveBreakdown(makePortfolio([pos]), spcxPrices);
    expect(total).toBeCloseTo((1.9 - 1.3) * 200 * 100, 6);
    expect(rows[0].col1).toBe("$1.30");
    expect(rows[0].col2).toBe("$1.90");
    expect(rows[0].pnlPct).toBeCloseTo((0.6 / 1.3) * 100, 6);
    expect(rows[0].pnlFlag).toBeUndefined();
  });

  test("combo missing one leg quote falls back to the IB-daily label with no %", () => {
    const pos = makeOptionPosition({ legs: spcxLegs, ib_daily_pnl: 25_232.97 });
    const partial = { SPCX_20261113_175_C: spcxPrices.SPCX_20261113_175_C };
    const { rows } = computeDayMoveBreakdown(makePortfolio([pos]), partial);
    expect(rows[0].col1).toBe("IB daily");
    expect(rows[0].col2).toBe("reqPnLSingle");
    expect(rows[0].pnlPct).toBeNull();
    expect(rows[0].pnlFlag).toBeUndefined();
  });

  test("a mid-resolved leg marks the current column as MID", () => {
    const pos = makeOptionPosition({ legs: spcxLegs, ib_daily_pnl: 25_232.97 });
    const prices = {
      SPCX_20261113_175_C: makePrice({ symbol: "SPCX_20261113_175_C", last: null, bid: 3.0, ask: 3.2, close: 1.8 }),
      SPCX_20261113_200_C: spcxPrices.SPCX_20261113_200_C,
    };
    const { rows } = computeDayMoveBreakdown(makePortfolio([pos]), prices);
    expect(rows[0].col2).toBe("$1.90 (MID)");
  });
});

describe("computeDayMoveBreakdown — IB daily vs quoted move disagreement", () => {
  const iwo = (ibDailyPnl: number) =>
    makeOptionPosition({
      id: 3,
      ticker: "IWO",
      structure: "Long Call $380.0",
      structure_type: "Long Call",
      contracts: 50,
      expiry: "2026-12-18",
      legs: [leg("LONG", "Call", 380, 50)],
      ib_daily_pnl: ibDailyPnl,
    });
  const iwoPrices = {
    IWO_20261218_380_C: makePrice({ symbol: "IWO_20261218_380_C", last: 2.95, close: 1.7 }),
  };

  test("flags the row when IB daily P&L and the quoted move disagree in sign", () => {
    const { rows } = computeDayMoveBreakdown(makePortfolio([iwo(-5_081.31)]), iwoPrices);
    expect(rows[0].pnl).toBe(-5_081.31);
    expect(rows[0].pnlFlag).toBeDefined();
    expect(rows[0].pnlFlag?.title).toContain("+$6,250.00");
  });

  test("does not flag when IB daily P&L agrees in sign with the quoted move", () => {
    const { rows } = computeDayMoveBreakdown(makePortfolio([iwo(5_900)]), iwoPrices);
    expect(rows[0].pnlFlag).toBeUndefined();
  });

  test("does not flag a stock row whose IB daily agrees with the quoted move", () => {
    const pos = makeOptionPosition({
      id: 4,
      ticker: "SOXL",
      structure: "Stock (8500.0 shares)",
      structure_type: "Stock",
      contracts: 8_500,
      expiry: "N/A",
      legs: [],
      ib_daily_pnl: 39_695.01,
    });
    const prices = { SOXL: makePrice({ symbol: "SOXL", last: 102.4, close: 97.55 }) };
    const { rows } = computeDayMoveBreakdown(makePortfolio([pos]), prices);
    expect(rows[0].pnlFlag).toBeUndefined();
  });

  test("flags a stock row whose IB daily disagrees with the quoted move", () => {
    const pos = makeOptionPosition({
      id: 4,
      ticker: "SOXL",
      structure: "Stock (8500.0 shares)",
      structure_type: "Stock",
      contracts: 8_500,
      expiry: "N/A",
      legs: [],
      ib_daily_pnl: -1_000,
    });
    const prices = { SOXL: makePrice({ symbol: "SOXL", last: 102.4, close: 97.55 }) };
    const { rows } = computeDayMoveBreakdown(makePortfolio([pos]), prices);
    expect(rows[0].pnlFlag).toBeDefined();
  });
});

describe("PnlBreakdownModal — flagged row", () => {
  test("renders the flag label with its explanation as the title", () => {
    render(
      <PnlBreakdownModal
        open
        title="Day Move: Intraday P&L"
        formula="test"
        col1Header="CLOSE"
        col2Header="CURRENT"
        rows={[
          {
            id: 1,
            ticker: "IWO",
            structure: "Long Call $380.0",
            col1: "$1.70",
            col2: "$2.95",
            pnl: -5_081.31,
            pnlPct: -59.8,
            pnlFlag: { label: "IB≠QUOTE", title: "IB daily -$5,081.31 disagrees with the quoted move +$6,250.00" },
          },
        ]}
        total={-5_081.31}
        onClose={() => undefined}
      />,
    );
    const flag = screen.getByText("IB≠QUOTE");
    expect(flag.getAttribute("title")).toContain("+$6,250.00");
  });
});
