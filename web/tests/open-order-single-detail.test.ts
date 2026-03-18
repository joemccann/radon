import { describe, expect, it } from "vitest";
import type { OpenOrder, PortfolioPosition } from "../lib/types";
import { buildOpenOrderDisplayRows } from "../lib/openOrderCombos";

function makeOptionOrder(overrides: Partial<OpenOrder> & { symbol?: string; strike?: number; right?: string; expiry?: string } = {}): OpenOrder {
  const symbol = overrides.symbol ?? "AAOI";
  return {
    orderId: overrides.orderId ?? 72,
    permId: overrides.permId ?? 653611397,
    symbol,
    contract: {
      conId: overrides.contract?.conId ?? 859556363,
      symbol,
      secType: "OPT",
      strike: overrides.contract?.strike ?? overrides.strike ?? 105,
      right: overrides.contract?.right ?? overrides.right ?? "C",
      expiry: overrides.contract?.expiry ?? overrides.expiry ?? "2026-03-20",
    },
    action: overrides.action ?? "SELL",
    orderType: overrides.orderType ?? "LMT",
    totalQuantity: overrides.totalQuantity ?? 50,
    limitPrice: overrides.limitPrice ?? 5,
    auxPrice: overrides.auxPrice ?? null,
    status: overrides.status ?? "Submitted",
    filled: overrides.filled ?? 0,
    remaining: overrides.remaining ?? 50,
    avgFillPrice: overrides.avgFillPrice ?? null,
    tif: overrides.tif ?? "DAY",
  };
}

function makeStockOrder(overrides: Partial<OpenOrder> & { symbol?: string } = {}): OpenOrder {
  const symbol = overrides.symbol ?? "TSLL";
  return {
    orderId: overrides.orderId ?? 10,
    permId: overrides.permId ?? 326482405,
    symbol,
    contract: {
      conId: overrides.contract?.conId ?? 578561429,
      symbol,
      secType: "STK",
      strike: null,
      right: null,
      expiry: null,
    },
    action: overrides.action ?? "SELL",
    orderType: overrides.orderType ?? "LMT",
    totalQuantity: overrides.totalQuantity ?? 5000,
    limitPrice: overrides.limitPrice ?? 21,
    auxPrice: overrides.auxPrice ?? null,
    status: overrides.status ?? "Submitted",
    filled: overrides.filled ?? 0,
    remaining: overrides.remaining ?? 5000,
    avgFillPrice: overrides.avgFillPrice ?? null,
    tif: overrides.tif ?? "GTC",
  };
}

function makeLongCallPosition(): PortfolioPosition {
  return {
    id: 1,
    ticker: "AAOI",
    structure: "Long Call",
    structure_type: "Long Call",
    risk_profile: "Defined",
    expiry: "2026-03-20",
    contracts: 50,
    direction: "LONG",
    entry_cost: 0,
    max_risk: null,
    market_value: 0,
    legs: [
      { direction: "LONG", contracts: 50, type: "Call", strike: 105, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
    ],
    ib_daily_pnl: null,
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-03-17",
  };
}

describe("buildOpenOrderDisplayRows single-leg detail", () => {
  it("adds option direction, strike, right, and expiry for single option orders", () => {
    const rows = buildOpenOrderDisplayRows(
      [makeOptionOrder()],
      [makeLongCallPosition()],
    );

    expect(rows).toHaveLength(1);
    const row = rows[0];
    expect(row.kind).toBe("single");
    if (row.kind !== "single") return;
    expect(row.summary).toBe("Long $105 Call 2026-03-20");
  });

  it("adds stock detail for single equity orders", () => {
    const rows = buildOpenOrderDisplayRows([makeStockOrder()]);

    expect(rows).toHaveLength(1);
    const row = rows[0];
    expect(row.kind).toBe("single");
    if (row.kind !== "single") return;
    expect(row.summary).toBe("Short Stock");
  });
});
