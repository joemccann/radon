/**
 * Journal Auto-Sync with IB — Red/Green TDD
 *
 * Tests the pure sync logic:
 * 1. syncNewTrades converts reconciliation trades to TradeEntry format
 * 2. Duplicate detection via fingerprint
 * 3. Correct cost calculations for STK, OPT, BAG
 * 4. IB_AUTO_IMPORT decision label
 */

import { describe, it, expect } from "vitest";
import { syncNewTrades, type ReconciliationTrade } from "@/lib/journalSync";

const MOCK_EXISTING = [
  {
    id: 1,
    date: "2026-03-02",
    ticker: "ALAB",
    structure: "Long Call - LEAP",
    decision: "EXECUTED",
    action: "TRADE",
    fill_price: 36.9,
    total_cost: 18452.24,
    contracts: 5,
  },
];

const STOCK_TRADE: ReconciliationTrade = {
  symbol: "ILF",
  date: "2026-03-09",
  action: "SELL",
  net_quantity: -2000,
  avg_price: 33.77,
  commission: 10.39,
  realized_pnl: -6835.27,
  sec_type: "STK",
};

const OPTION_TRADE: ReconciliationTrade = {
  symbol: "BKD",
  date: "2026-03-09",
  action: "CLOSED",
  net_quantity: 0,
  avg_price: 0.725,
  commission: 124.92,
  realized_pnl: 2296.80,
  sec_type: "OPT",
};

const BAG_TRADE: ReconciliationTrade = {
  symbol: "SPXU",
  date: "2026-03-09",
  action: "BUY_OPTION",
  net_quantity: 20,
  avg_price: 1.077,
  commission: 84.15,
  realized_pnl: -317.72,
  sec_type: "BAG",
};

const BUY_STOCK: ReconciliationTrade = {
  symbol: "URTY",
  date: "2026-03-09",
  action: "BUY",
  net_quantity: 2000,
  avg_price: 55.997,
  commission: 10.0,
  realized_pnl: 0,
  sec_type: "STK",
};

describe("syncNewTrades", () => {
  it("imports stock trades with correct structure", () => {
    const result = syncNewTrades(MOCK_EXISTING, [BUY_STOCK]);

    expect(result.imported).toBe(1);
    expect(result.skipped).toBe(0);
    expect(result.trades).toHaveLength(1);

    const t = result.trades[0];
    expect(t.ticker).toBe("URTY");
    expect(t.decision).toBe("IB_AUTO_IMPORT");
    expect(t.action).toBe("BUY");
    expect(t.structure).toContain("Stock");
    expect(t.structure).toContain("STK");
    expect(t.shares).toBe(2000);
    expect(t.contracts).toBeUndefined();
    expect(t.fill_price).toBe(55.997);
    // STK cost: |2000| * 55.997 * 1 + 10.0 commission
    expect(t.total_cost).toBeCloseTo(2000 * 55.997 + 10.0, 2);
    expect(t.id).toBe(2); // next after max existing id=1
  });

  it("imports option trades with 100x multiplier", () => {
    const result = syncNewTrades(MOCK_EXISTING, [OPTION_TRADE]);

    expect(result.imported).toBe(1);
    const t = result.trades[0];
    expect(t.ticker).toBe("BKD");
    expect(t.structure).toContain("Option");
    expect(t.structure).toContain("OPT");
    // OPT with qty=0 (closed): |0| * 0.725 * 100 + 124.92 = 124.92
    expect(t.total_cost).toBeCloseTo(124.92, 2);
    expect(t.realized_pnl).toBe(2296.80);
    expect(t.contracts).toBe(0);
  });

  it("imports BAG/spread trades with 100x multiplier", () => {
    const result = syncNewTrades(MOCK_EXISTING, [BAG_TRADE]);

    expect(result.imported).toBe(1);
    const t = result.trades[0];
    expect(t.ticker).toBe("SPXU");
    expect(t.structure).toContain("Spread");
    expect(t.structure).toContain("BAG");
    // BAG: |20| * 1.077 * 100 + 84.15
    expect(t.total_cost).toBeCloseTo(20 * 1.077 * 100 + 84.15, 2);
    expect(t.contracts).toBe(20);
  });

  it("imports sold stock trades with realized P&L", () => {
    const result = syncNewTrades(MOCK_EXISTING, [STOCK_TRADE]);

    expect(result.imported).toBe(1);
    const t = result.trades[0];
    expect(t.ticker).toBe("ILF");
    expect(t.action).toBe("SELL");
    expect(t.structure).toContain("Closed");
    expect(t.realized_pnl).toBe(-6835.27);
    expect(t.shares).toBe(2000); // absolute value
    // STK: |-2000| * 33.77 * 1 + 10.39
    expect(t.total_cost).toBeCloseTo(2000 * 33.77 + 10.39, 2);
  });

  it("imports multiple trades with sequential IDs", () => {
    const result = syncNewTrades(MOCK_EXISTING, [BUY_STOCK, STOCK_TRADE, BAG_TRADE]);

    expect(result.imported).toBe(3);
    expect(result.trades[0].id).toBe(2);
    expect(result.trades[1].id).toBe(3);
    expect(result.trades[2].id).toBe(4);
  });

  it("prevents duplicate imports (idempotent)", () => {
    // First import
    const r1 = syncNewTrades(MOCK_EXISTING, [BUY_STOCK]);
    expect(r1.imported).toBe(1);

    // Merge into existing
    const updatedExisting = [...MOCK_EXISTING, ...r1.trades];

    // Second import with same trades — should skip
    const r2 = syncNewTrades(updatedExisting, [BUY_STOCK]);
    expect(r2.imported).toBe(0);
    expect(r2.skipped).toBe(1);
    expect(r2.trades).toHaveLength(0);
  });

  it("handles empty new_trades array", () => {
    const result = syncNewTrades(MOCK_EXISTING, []);
    expect(result.imported).toBe(0);
    expect(result.skipped).toBe(0);
    expect(result.trades).toHaveLength(0);
  });

  it("handles empty existing trades", () => {
    const result = syncNewTrades([], [BUY_STOCK]);
    expect(result.imported).toBe(1);
    expect(result.trades[0].id).toBe(1);
  });

  it("skips zero realized_pnl from output entry", () => {
    const result = syncNewTrades([], [BUY_STOCK]);
    // BUY_STOCK has realized_pnl: 0, should not be in the entry
    expect(result.trades[0].realized_pnl).toBeUndefined();
  });

  it("includes auto-import note with date", () => {
    const result = syncNewTrades([], [BUY_STOCK]);
    expect(result.trades[0].notes).toContain("Auto-imported from IB");
  });

  it("negative quantity fingerprint matches positive stored quantity", () => {
    // Import a SELL trade with negative net_quantity
    const r1 = syncNewTrades([], [STOCK_TRADE]); // net_quantity: -2000
    expect(r1.imported).toBe(1);
    expect(r1.trades[0].shares).toBe(2000); // stored as abs

    // Try re-importing — should match despite -2000 vs stored 2000
    const r2 = syncNewTrades(r1.trades, [STOCK_TRADE]);
    expect(r2.imported).toBe(0);
    expect(r2.skipped).toBe(1);
  });

  it("uses contract details in structure when available", () => {
    const optionWithDetails: ReconciliationTrade = {
      symbol: "EWY",
      date: "2026-03-10",
      action: "BUY_OPTION",
      net_quantity: 25,
      avg_price: 2.0,
      commission: 7.55,
      realized_pnl: 0,
      sec_type: "OPT",
      strike: 130,
      expiry: "20260313",
      right: "P",
    };
    const result = syncNewTrades([], [optionWithDetails]);
    expect(result.imported).toBe(1);
    const t = result.trades[0];
    expect(t.structure).toBe("Long Put $130 2026-03-13");
  });

  it("falls back to generic structure without contract details", () => {
    const result = syncNewTrades(MOCK_EXISTING, [OPTION_TRADE]);
    expect(result.imported).toBe(1);
    // OPTION_TRADE has no strike/expiry/right
    expect(result.trades[0].structure).toContain("Option");
    expect(result.trades[0].structure).toContain("OPT");
  });

  it("mixed new and duplicate trades", () => {
    // Import BUY_STOCK first
    const r1 = syncNewTrades(MOCK_EXISTING, [BUY_STOCK]);
    const updated = [...MOCK_EXISTING, ...r1.trades];

    // Now try BUY_STOCK (dup) + BAG_TRADE (new)
    const r2 = syncNewTrades(updated, [BUY_STOCK, BAG_TRADE]);
    expect(r2.imported).toBe(1);
    expect(r2.skipped).toBe(1);
    expect(r2.trades[0].ticker).toBe("SPXU");
  });
});
