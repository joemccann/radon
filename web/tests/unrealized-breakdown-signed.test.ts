/**
 * Regression: Unrealized P&L modal used Math.abs on ENTRY COST and MKT VALUE,
 * so credit entries and short market marks could not be reconciled with P&L.
 *
 * Example (2026-07-30 screenshot):
 *   META combo: entry +$12,014.92, MV −$34,660 → P&L −$46,674.92
 *   Shown as both positive → looked like 34660 − 12015 should be a gain.
 *
 * Fix: format signed entry and market value so P&L = MKT − ENTRY is obvious.
 */

import { describe, expect, test } from "vitest";
import {
  computeUnrealizedBreakdown,
  sumUnrealizedBreakdown,
} from "../lib/unrealizedBreakdown";
import type { PortfolioData, PortfolioPosition } from "../lib/types";

function makePortfolio(positions: PortfolioPosition[]): PortfolioData {
  return {
    bankroll: 1_350_000,
    peak_value: 1_350_000,
    last_sync: "2026-07-30T10:59:00.000Z",
    total_deployed_pct: 0,
    total_deployed_dollars: 0,
    remaining_capacity_pct: 100,
    position_count: positions.length,
    defined_risk_count: positions.length,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
    positions,
    account_summary: {
      net_liquidation: 1_350_000,
      daily_pnl: 0,
      unrealized_pnl: -281_952.32,
      realized_pnl: 0,
      settled_cash: 0,
      maintenance_margin: 0,
      excess_liquidity: 0,
      buying_power: 0,
      dividends: 0,
    },
  } as PortfolioData;
}

describe("computeUnrealizedBreakdown — signed entry and market value", () => {
  test("META net-short combo: shows negative MV so P&L = MV − entry is verifiable", () => {
    // Debit entry, short-heavy mark (MV negative) → large open loss
    const pos: PortfolioPosition = {
      id: 1,
      ticker: "META",
      structure: "Combo (3 legs)",
      structure_type: "Combo",
      risk_profile: "undefined",
      expiry: "2026-08-15",
      contracts: 20,
      direction: "LONG",
      entry_cost: 12_014.92,
      max_risk: null,
      market_value: -34_660.0,
      legs: [
        {
          direction: "LONG",
          contracts: 20,
          type: "Call",
          strike: 620,
          entry_cost: 45_168.5,
          avg_cost: 22.58,
          market_price: 2.46,
          market_value: 4_920,
        },
        {
          direction: "SHORT",
          contracts: 20,
          type: "Call",
          strike: 680,
          entry_cost: 15_768.89,
          avg_cost: 7.88,
          market_price: 0.78,
          market_value: 1_560,
        },
        {
          direction: "SHORT",
          contracts: 10,
          type: "Put",
          strike: 560,
          entry_cost: 17_384.69,
          avg_cost: 17.38,
          market_price: 38.0,
          market_value: 38_000,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-07-28",
    };

    const rows = computeUnrealizedBreakdown(makePortfolio([pos]));
    expect(rows).toHaveLength(1);
    const row = rows[0];

    // Multi-leg resolveEntryCost / resolveMarketValue recompute from legs:
    // entry: +45168.50 - 15768.89 - 17384.69 = +12014.92
    // mv:    +4920 - 1560 - 38000 = -34640
    expect(row.pnl).toBeCloseTo(-34_640 - 12_014.92, 2);
    expect(row.col1).toBe("+$12,014.92");
    expect(row.col2).toMatch(/^-\$34,6/); // negative market mark visible
    expect(row.col2).not.toMatch(/^\+\$34/); // must not strip the short mark

    // Eye-check: parse signed dollars and recompute
    const parseSigned = (s: string) => Number(s.replace(/[$,+]/g, ""));
    expect(parseSigned(row.col2) - parseSigned(row.col1)).toBeCloseTo(row.pnl, 2);
  });

  test("AAOI credit RR: shows negative entry so P&L = MV − entry is verifiable", () => {
    const pos: PortfolioPosition = {
      id: 2,
      ticker: "AAOI",
      structure: "Risk Reversal (P$80.0/C$90.0)",
      structure_type: "Risk Reversal",
      risk_profile: "undefined",
      expiry: "2026-08-15",
      contracts: 100,
      direction: "LONG",
      entry_cost: -9_002.57,
      max_risk: null,
      market_value: 32_000,
      legs: [
        {
          direction: "LONG",
          contracts: 100,
          type: "Call",
          strike: 90,
          entry_cost: 100_007.91,
          avg_cost: 10,
          market_price: 12.5,
          market_value: 125_000,
        },
        {
          direction: "SHORT",
          contracts: 100,
          type: "Put",
          strike: 80,
          entry_cost: 109_010.48,
          avg_cost: 10.9,
          market_price: 9.3,
          market_value: 93_000,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-07-28",
    };

    const rows = computeUnrealizedBreakdown(makePortfolio([pos]));
    expect(rows).toHaveLength(1);
    const row = rows[0];

    // entry: +100007.91 - 109010.48 = -9002.57
    // mv: +125000 - 93000 = 32000
    // pnl: 32000 - (-9002.57) = 41002.57
    expect(row.pnl).toBeCloseTo(41_002.57, 2);
    expect(row.col1).toBe("-$9,002.57");
    expect(row.col2).toBe("+$32,000.00");

    const parseSigned = (s: string) => Number(s.replace(/[$,+]/g, ""));
    expect(parseSigned(row.col2) - parseSigned(row.col1)).toBeCloseTo(row.pnl, 2);
  });

  test("long stock: positive entry and MV; P&L still MV − entry", () => {
    const pos: PortfolioPosition = {
      id: 3,
      ticker: "MSFT",
      structure: "Stock (1000.0 shares)",
      structure_type: "Stock",
      risk_profile: "equity",
      expiry: "N/A",
      contracts: 1000,
      direction: "LONG",
      entry_cost: 468_505.27,
      max_risk: null,
      market_value: 448_930,
      legs: [
        {
          direction: "LONG",
          contracts: 1000,
          type: "Stock",
          strike: 0,
          entry_cost: 468_505.27,
          avg_cost: 468.505,
          market_price: 448.93,
          market_value: 448_930,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-07-09",
    };

    const rows = computeUnrealizedBreakdown(makePortfolio([pos]));
    expect(rows[0].col1).toBe("+$468,505.27");
    expect(rows[0].col2).toBe("+$448,930.00");
    expect(rows[0].pnl).toBeCloseTo(448_930 - 468_505.27, 2);
  });

  test("sumUnrealizedBreakdown matches row P&L total", () => {
    const positions: PortfolioPosition[] = [
      {
        id: 1,
        ticker: "SOFI",
        structure: "Long Call $45.0",
        structure_type: "Long Call",
        risk_profile: "defined",
        expiry: "2027-01-15",
        contracts: 300,
        direction: "LONG",
        entry_cost: 44_383.38,
        max_risk: 44_383.38,
        market_value: 3_150,
        legs: [
          {
            direction: "LONG",
            contracts: 300,
            type: "Call",
            strike: 45,
            entry_cost: 44_383.38,
            avg_cost: 1.48,
            market_price: 0.105,
            market_value: 3_150,
          },
        ],
        kelly_optimal: null,
        target: null,
        stop: null,
        entry_date: "2026-07-09",
      },
      {
        id: 2,
        ticker: "WULF",
        structure: "Long Call $17.0",
        structure_type: "Long Call",
        risk_profile: "defined",
        expiry: "2027-01-15",
        contracts: 77,
        direction: "LONG",
        entry_cost: 40_076.51,
        max_risk: 40_076.51,
        market_value: 40_040,
        legs: [
          {
            direction: "LONG",
            contracts: 77,
            type: "Call",
            strike: 17,
            entry_cost: 40_076.51,
            avg_cost: 5.2,
            market_price: 5.2,
            market_value: 40_040,
          },
        ],
        kelly_optimal: null,
        target: null,
        stop: null,
        entry_date: "2026-07-09",
      },
    ];
    const portfolio = makePortfolio(positions);
    const rows = computeUnrealizedBreakdown(portfolio);
    const rowSum = rows.reduce((s, r) => s + r.pnl, 0);
    expect(sumUnrealizedBreakdown(portfolio)).toBeCloseTo(rowSum, 2);
    expect(rowSum).toBeCloseTo((3_150 - 44_383.38) + (40_040 - 40_076.51), 2);
  });
});
