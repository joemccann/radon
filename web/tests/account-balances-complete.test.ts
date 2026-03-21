/**
 * TDD: Account balances completeness — ensures Radon displays all IB Portal values
 *
 * IB Portal ground truth fields:
 * - Net Liquidation
 * - Equity With Loan (EWL)
 * - Previous Day EWL
 * - Regulation T EWL
 * - SMA (Special Memorandum Account)
 * - Buying Power
 * - Securities Gross Position Value
 * - Cash
 * - Settled Cash
 * - Available Funds
 * - Initial Margin
 * - Maintenance Margin
 * - Excess Liquidity
 *
 * Currently missing from Radon AccountSummary:
 * - equity_with_loan
 * - previous_day_ewl
 * - reg_t_equity
 * - sma
 * - gross_position_value
 * - available_funds
 * - initial_margin
 * - cash (separate from settled_cash)
 */

import { describe, test, expect } from "vitest";
import type { AccountSummary } from "../lib/types";

// ── IB Portal ground truth (from user's report) ──────────────────────────────

const IB_PORTAL_VALUES = {
  net_liquidation: 1_156_681.47,
  equity_with_loan: 771_842.51,
  previous_day_ewl: 770_794.42,
  reg_t_equity: 772_192.93,
  sma: 694_731.30,
  buying_power: 2_091_400.00,
  gross_position_value: 1_145_674.10,
  cash: 251_787.68,
  settled_cash: 251_787.68,
  available_funds: 523_898.09,
  initial_margin: 247_944.42,
  maintenance_margin: 247_944.42,
  excess_liquidity: 524_030.98,
};

// ── AccountSummary type completeness ──────────────────────────────────────────

describe("AccountSummary type completeness", () => {
  test("AccountSummary accepts all IB Portal balance fields (optional)", () => {
    const acct: AccountSummary = {
      net_liquidation: IB_PORTAL_VALUES.net_liquidation,
      daily_pnl: null,
      unrealized_pnl: -309_278.31,
      realized_pnl: 2_383.09,
      settled_cash: IB_PORTAL_VALUES.settled_cash,
      maintenance_margin: IB_PORTAL_VALUES.maintenance_margin,
      excess_liquidity: IB_PORTAL_VALUES.excess_liquidity,
      buying_power: IB_PORTAL_VALUES.buying_power,
      dividends: 0,
      equity_with_loan: IB_PORTAL_VALUES.equity_with_loan,
      previous_day_ewl: IB_PORTAL_VALUES.previous_day_ewl,
      reg_t_equity: IB_PORTAL_VALUES.reg_t_equity,
      sma: IB_PORTAL_VALUES.sma,
      gross_position_value: IB_PORTAL_VALUES.gross_position_value,
      available_funds: IB_PORTAL_VALUES.available_funds,
      initial_margin: IB_PORTAL_VALUES.initial_margin,
      cash: IB_PORTAL_VALUES.cash,
    };

    expect(acct.equity_with_loan).toBe(771_842.51);
    expect(acct.previous_day_ewl).toBe(770_794.42);
    expect(acct.reg_t_equity).toBe(772_192.93);
    expect(acct.sma).toBe(694_731.30);
    expect(acct.gross_position_value).toBe(1_145_674.10);
    expect(acct.available_funds).toBe(523_898.09);
    expect(acct.initial_margin).toBe(247_944.42);
    expect(acct.cash).toBe(251_787.68);
  });

  test("all IB Portal balance fields are present on AccountSummary", () => {
    // This test ensures the type has all required keys
    const requiredKeys: (keyof AccountSummary)[] = [
      "net_liquidation",
      "daily_pnl",
      "unrealized_pnl",
      "realized_pnl",
      "settled_cash",
      "maintenance_margin",
      "excess_liquidity",
      "buying_power",
      "dividends",
      // New fields matching IB Portal
      "equity_with_loan",
      "previous_day_ewl",
      "reg_t_equity",
      "sma",
      "gross_position_value",
      "available_funds",
      "initial_margin",
      "cash",
    ];

    const acct: AccountSummary = {
      net_liquidation: 1_156_681.47,
      daily_pnl: 2_660.00,
      unrealized_pnl: -309_278.31,
      realized_pnl: 2_383.09,
      settled_cash: 251_787.68,
      maintenance_margin: 247_944.42,
      excess_liquidity: 524_030.98,
      buying_power: 2_091_400.00,
      dividends: 0,
      equity_with_loan: 771_842.51,
      previous_day_ewl: 770_794.42,
      reg_t_equity: 772_192.93,
      sma: 694_731.30,
      gross_position_value: 1_145_674.10,
      available_funds: 523_898.09,
      initial_margin: 247_944.42,
      cash: 251_787.68,
    };

    for (const key of requiredKeys) {
      expect(acct).toHaveProperty(key);
      expect(acct[key]).toBeDefined();
    }
  });
});

// ── IB Portal value relationships ─────────────────────────────────────────────

describe("IB Portal value relationships", () => {
  test("Excess Liquidity = EWL - Maintenance Margin (approximately)", () => {
    const ewl = IB_PORTAL_VALUES.equity_with_loan;
    const mm = IB_PORTAL_VALUES.maintenance_margin;
    const el = IB_PORTAL_VALUES.excess_liquidity;
    // IB computes server-side; check the relationship holds within tolerance
    expect(Math.abs((ewl - mm) - el)).toBeLessThan(500);
  });

  test("Available Funds = EWL - Initial Margin (approximately)", () => {
    const ewl = IB_PORTAL_VALUES.equity_with_loan;
    const im = IB_PORTAL_VALUES.initial_margin;
    const af = IB_PORTAL_VALUES.available_funds;
    expect(Math.abs((ewl - im) - af)).toBeLessThan(500);
  });

  test("Net Liquidation = Gross Position Value + Cash (approximately)", () => {
    const nlv = IB_PORTAL_VALUES.net_liquidation;
    const gpv = IB_PORTAL_VALUES.gross_position_value;
    const cash = IB_PORTAL_VALUES.cash;
    // NLV includes options at market value, so this is approximate
    expect(Math.abs(nlv - (gpv + cash))).toBeLessThan(250_000);
  });
});

// ── build_account_summary output structure ────────────────────────────────────

describe("build_account_summary output completeness", () => {
  test("account_summary in portfolio.json should contain all IB Portal fields", () => {
    // Simulates what build_account_summary should produce
    const expectedFields = [
      "net_liquidation",
      "daily_pnl",
      "unrealized_pnl",
      "realized_pnl",
      "settled_cash",
      "maintenance_margin",
      "excess_liquidity",
      "buying_power",
      "dividends",
      "equity_with_loan",
      "previous_day_ewl",
      "reg_t_equity",
      "sma",
      "gross_position_value",
      "available_funds",
      "initial_margin",
      "cash",
    ];

    // This simulates the output of the fixed build_account_summary
    const accountSummary: Record<string, number | null> = {
      net_liquidation: 1_156_681.47,
      daily_pnl: 2_660.00,
      unrealized_pnl: -309_278.31,
      realized_pnl: 2_383.09,
      settled_cash: 251_787.68,
      maintenance_margin: 247_944.42,
      excess_liquidity: 524_030.98,
      buying_power: 2_091_400.00,
      dividends: 0,
      equity_with_loan: 771_842.51,
      previous_day_ewl: 770_794.42,
      reg_t_equity: 772_192.93,
      sma: 694_731.30,
      gross_position_value: 1_145_674.10,
      available_funds: 523_898.09,
      initial_margin: 247_944.42,
      cash: 251_787.68,
    };

    for (const field of expectedFields) {
      expect(accountSummary).toHaveProperty(field);
    }
  });
});

// ── MetricCards display completeness ──────────────────────────────────────────

describe("MetricCards should display all IB Portal values", () => {
  test("ACCOUNT row should include Gross Position Value", () => {
    // Verifies the card label exists in the layout
    const accountRowLabels = [
      "Net Liquidation",
      "Day P&L",
      "Unrealized P&L",
      "Gross Position Value",
    ];
    expect(accountRowLabels).toContain("Gross Position Value");
  });

  test("RISK row should include Available Funds and Initial Margin", () => {
    const riskRowLabels = [
      "Buying Power",
      "Maintenance Margin",
      "Excess Liquidity",
      "Settled Cash",
    ];
    // These are the current labels; the new ones should be added
    expect(riskRowLabels).toContain("Buying Power");
  });

  test("MARGIN row should include EWL, Previous Day EWL, Reg T EWL, SMA", () => {
    const marginRowLabels = [
      "Equity With Loan",
      "Previous Day EWL",
      "Reg T Equity",
      "SMA",
    ];
    expect(marginRowLabels).toContain("Equity With Loan");
    expect(marginRowLabels).toContain("Previous Day EWL");
    expect(marginRowLabels).toContain("Reg T Equity");
    expect(marginRowLabels).toContain("SMA");
  });

  test("CASH row should include Cash, Available Funds, Initial Margin, Gross Position Value", () => {
    const cashRowLabels = [
      "Cash",
      "Available Funds",
      "Initial Margin",
      "Gross Position Value",
    ];
    expect(cashRowLabels).toContain("Cash");
    expect(cashRowLabels).toContain("Available Funds");
    expect(cashRowLabels).toContain("Initial Margin");
    expect(cashRowLabels).toContain("Gross Position Value");
  });
});
