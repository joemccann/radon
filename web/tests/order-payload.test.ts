import { describe, it, expect } from "vitest";
import { buildSingleLegOrderPayload } from "../components/ticker-detail/OrderTab";
import type { PortfolioPosition } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makePutPosition(overrides: Partial<PortfolioPosition> = {}): PortfolioPosition {
  return {
    id: 1,
    ticker: "BKD",
    structure: "Long Put",
    structure_type: "Long Put",
    risk_profile: "defined",
    expiry: "2026-04-17",
    contracts: 100,
    direction: "LONG",
    entry_cost: 13500,
    max_risk: 13500,
    market_value: 13500,
    legs: [
      {
        direction: "LONG",
        contracts: 100,
        type: "Put",
        strike: 12.5,
        entry_cost: 13500,
        avg_cost: 135,
        market_price: 1.35,
        market_value: 13500,
      },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-03-09",
    ...overrides,
  };
}

function makeCallPosition(overrides: Partial<PortfolioPosition> = {}): PortfolioPosition {
  return {
    id: 2,
    ticker: "AAOI",
    structure: "Long Call",
    structure_type: "Long Call",
    risk_profile: "defined",
    expiry: "2026-03-20",
    contracts: 100,
    direction: "LONG",
    entry_cost: 90970,
    max_risk: 90970,
    market_value: 87000,
    legs: [
      {
        direction: "LONG",
        contracts: 100,
        type: "Call",
        strike: 105.0,
        entry_cost: 90970,
        avg_cost: 909.7,
        market_price: 8.7,
        market_value: 87000,
      },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-03-09",
    ...overrides,
  };
}

function makeStockPosition(overrides: Partial<PortfolioPosition> = {}): PortfolioPosition {
  return {
    id: 3,
    ticker: "AAPL",
    structure: "Stock",
    structure_type: "Stock",
    risk_profile: "undefined",
    expiry: "",
    contracts: 100,
    direction: "LONG",
    entry_cost: 22500,
    max_risk: null,
    market_value: 22500,
    legs: [
      {
        direction: "LONG",
        contracts: 100,
        type: "Stock",
        strike: null,
        entry_cost: 22500,
        avg_cost: 225,
        market_price: 225,
        market_value: 22500,
      },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-03-09",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests: single-leg option (PUT) — the BKD bug scenario
// ---------------------------------------------------------------------------

describe("buildSingleLegOrderPayload — single-leg put option", () => {
  it("sends type=option (not stock) for a single-leg put position", () => {
    const payload = buildSingleLegOrderPayload({
      ticker: "BKD",
      action: "SELL",
      quantity: 100,
      limitPrice: 1.35,
      tif: "DAY",
      position: makePutPosition(),
    });
    expect(payload.type).toBe("option");
  });

  it("includes expiry, strike, and right=P for puts", () => {
    const payload = buildSingleLegOrderPayload({
      ticker: "BKD",
      action: "SELL",
      quantity: 100,
      limitPrice: 1.35,
      tif: "DAY",
      position: makePutPosition(),
    });
    expect(payload.expiry).toBe("20260417");
    expect(payload.strike).toBe(12.5);
    expect(payload.right).toBe("P");
  });

  it("normalizes expiry from YYYY-MM-DD to YYYYMMDD", () => {
    const payload = buildSingleLegOrderPayload({
      ticker: "BKD",
      action: "SELL",
      quantity: 100,
      limitPrice: 1.35,
      tif: "DAY",
      position: makePutPosition({ expiry: "2026-04-17" }),
    });
    expect(payload.expiry).toBe("20260417");
  });

  it("preserves already-clean YYYYMMDD expiry unchanged", () => {
    const payload = buildSingleLegOrderPayload({
      ticker: "BKD",
      action: "SELL",
      quantity: 100,
      limitPrice: 1.35,
      tif: "DAY",
      position: makePutPosition({ expiry: "20260417" }),
    });
    expect(payload.expiry).toBe("20260417");
  });

  it("does NOT send stock-only type for a put option order", () => {
    const payload = buildSingleLegOrderPayload({
      ticker: "BKD",
      action: "SELL",
      quantity: 100,
      limitPrice: 1.35,
      tif: "DAY",
      position: makePutPosition(),
    });
    // This is the root cause of the bug: must not be "stock"
    expect(payload.type).not.toBe("stock");
  });
});

// ---------------------------------------------------------------------------
// Tests: single-leg option (CALL)
// ---------------------------------------------------------------------------

describe("buildSingleLegOrderPayload — single-leg call option", () => {
  it("sends type=option with right=C for calls", () => {
    const payload = buildSingleLegOrderPayload({
      ticker: "AAOI",
      action: "SELL",
      quantity: 50,
      limitPrice: 9.0,
      tif: "GTC",
      position: makeCallPosition(),
    });
    expect(payload.type).toBe("option");
    expect(payload.right).toBe("C");
    expect(payload.strike).toBe(105.0);
    expect(payload.expiry).toBe("20260320");
  });

  it("passes through symbol, action, quantity, limitPrice, tif", () => {
    const payload = buildSingleLegOrderPayload({
      ticker: "AAOI",
      action: "BUY",
      quantity: 10,
      limitPrice: 7.5,
      tif: "GTC",
      position: makeCallPosition(),
    });
    expect(payload.symbol).toBe("AAOI");
    expect(payload.action).toBe("BUY");
    expect(payload.quantity).toBe(10);
    expect(payload.limitPrice).toBe(7.5);
    expect(payload.tif).toBe("GTC");
  });
});

// ---------------------------------------------------------------------------
// Tests: stock position — must remain type=stock
// ---------------------------------------------------------------------------

describe("buildSingleLegOrderPayload — stock position", () => {
  it("sends type=stock for a stock position", () => {
    const payload = buildSingleLegOrderPayload({
      ticker: "AAPL",
      action: "SELL",
      quantity: 100,
      limitPrice: 225.0,
      tif: "DAY",
      position: makeStockPosition(),
    });
    expect(payload.type).toBe("stock");
  });

  it("does not include expiry/strike/right for stock orders", () => {
    const payload = buildSingleLegOrderPayload({
      ticker: "AAPL",
      action: "SELL",
      quantity: 100,
      limitPrice: 225.0,
      tif: "DAY",
      position: makeStockPosition(),
    });
    expect(payload.expiry).toBeUndefined();
    expect(payload.strike).toBeUndefined();
    expect(payload.right).toBeUndefined();
  });

  it("passes orderType and stopPrice for a stock stop", () => {
    const payload = buildSingleLegOrderPayload({
      ticker: "AAPL",
      action: "SELL",
      quantity: 100,
      limitPrice: 168,
      tif: "GTC",
      position: makeStockPosition(),
      orderType: "STP",
      stopPrice: 170,
    });
    expect(payload.orderType).toBe("STP");
    expect(payload.stopPrice).toBe(170);
    expect(payload.limitPrice).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Tests: no position (new order with no existing position)
// ---------------------------------------------------------------------------

describe("buildSingleLegOrderPayload — no position (null)", () => {
  it("sends type=stock when position is null", () => {
    const payload = buildSingleLegOrderPayload({
      ticker: "MSFT",
      action: "BUY",
      quantity: 50,
      limitPrice: 400.0,
      tif: "DAY",
      position: null,
    });
    expect(payload.type).toBe("stock");
  });

  it("does not include option fields when position is null", () => {
    const payload = buildSingleLegOrderPayload({
      ticker: "MSFT",
      action: "BUY",
      quantity: 50,
      limitPrice: 400.0,
      tif: "DAY",
      position: null,
    });
    expect(payload.expiry).toBeUndefined();
    expect(payload.strike).toBeUndefined();
    expect(payload.right).toBeUndefined();
  });
});
