/**
 * Operator-facing display helpers for the /orders surface.
 * Pure functions only — no React.
 */
import { describe, expect, it } from "vitest";
import type { OpenOrder, PortfolioPosition } from "../lib/types";
import {
  cardToneForIntent,
  distanceToFill,
  formatFillQuantity,
  isPartialFill,
  mapOrderStatus,
  resolveOrderIntent,
  summarizeOpenOrders,
} from "../lib/orders/orderDisplay";

function makeOrder(overrides: Partial<OpenOrder> = {}): OpenOrder {
  const totalQuantity = overrides.totalQuantity ?? 10;
  return {
    orderId: overrides.orderId ?? 1,
    permId: overrides.permId ?? 1001,
    symbol: overrides.symbol ?? "AAPL",
    contract: overrides.contract ?? {
      conId: 1,
      symbol: "AAPL",
      secType: "OPT",
      strike: 200,
      right: "C",
      expiry: "2026-08-21",
    },
    action: overrides.action ?? "BUY",
    orderType: overrides.orderType ?? "LMT",
    totalQuantity,
    limitPrice: overrides.limitPrice ?? 1.5,
    auxPrice: overrides.auxPrice ?? null,
    status: overrides.status ?? "Submitted",
    filled: overrides.filled ?? 0,
    remaining: overrides.remaining ?? totalQuantity,
    avgFillPrice: overrides.avgFillPrice ?? null,
    tif: overrides.tif ?? "DAY",
  };
}

function makeStockPosition(
  ticker: string,
  direction: "LONG" | "SHORT",
  contracts = 100,
): PortfolioPosition {
  return {
    id: 1,
    ticker,
    structure: "Stock",
    structure_type: "stock",
    risk_profile: "undefined",
    expiry: "",
    contracts,
    direction,
    entry_cost: 10_000,
    max_risk: 10_000,
    market_value: 10_000,
    legs: [
      {
        type: "Stock",
        strike: null,
        direction,
        contracts,
        entry_cost: 100,
        avg_cost: 100,
        market_price: 100,
        market_value: 10_000,
      },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-01-01",
  };
}

function makeOptPosition(
  ticker: string,
  direction: "LONG" | "SHORT",
  strike: number,
  right: "Call" | "Put",
  expiry = "2026-08-21",
): PortfolioPosition {
  return {
    id: 2,
    ticker,
    structure: right,
    structure_type: "option",
    risk_profile: "defined",
    expiry,
    contracts: 5,
    direction,
    entry_cost: 750,
    max_risk: 750,
    market_value: 750,
    legs: [
      {
        type: right,
        strike,
        direction,
        contracts: 5,
        entry_cost: 150,
        avg_cost: 150,
        market_price: 1.5,
        market_value: 750,
      },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-01-01",
  };
}

describe("isPartialFill", () => {
  it("true when filled and remaining both positive", () => {
    expect(isPartialFill({ filled: 3, remaining: 7 })).toBe(true);
  });

  it("false when nothing filled or fully filled", () => {
    expect(isPartialFill({ filled: 0, remaining: 10 })).toBe(false);
    expect(isPartialFill({ filled: 10, remaining: 0 })).toBe(false);
  });
});

describe("formatFillQuantity", () => {
  it("shows filled/total when partial", () => {
    expect(
      formatFillQuantity({ filled: 3, remaining: 7, totalQuantity: 10 }),
    ).toBe("3/10");
  });

  it("shows total only when not partial", () => {
    expect(
      formatFillQuantity({ filled: 0, remaining: 10, totalQuantity: 10 }),
    ).toBe("10");
  });
});

describe("mapOrderStatus", () => {
  it("maps Submitted/PreSubmitted to Working/Queued", () => {
    expect(mapOrderStatus("Submitted").label).toBe("Working");
    expect(mapOrderStatus("PreSubmitted").label).toBe("Queued");
    expect(mapOrderStatus("Submitted").raw).toBe("Submitted");
  });

  it("surfaces Partial when fill progress is partial, overriding raw status", () => {
    const mapped = mapOrderStatus("Submitted", { filled: 2, remaining: 3 });
    expect(mapped.label).toBe("Partial");
    expect(mapped.tone).toBe("partial");
  });

  it("surfaces pending cancel/modify over raw status", () => {
    expect(mapOrderStatus("Submitted", { isPendingCancel: true }).label).toBe(
      "Pending cancel",
    );
    expect(mapOrderStatus("Submitted", { isPendingModify: true }).label).toBe(
      "Pending modify",
    );
  });

  it("maps inactive and cancelled family", () => {
    expect(mapOrderStatus("Inactive").label).toBe("Inactive");
    expect(mapOrderStatus("Cancelled").label).toBe("Cancelled");
    expect(mapOrderStatus("ApiCancelled").label).toBe("Cancelled");
  });
});

describe("distanceToFill", () => {
  it("BUY limit: market above limit is away; market at/below is through", () => {
    const away = distanceToFill({
      action: "BUY",
      limitPrice: 1.5,
      lastPrice: 1.8,
    });
    expect(away).not.toBeNull();
    expect(away!.delta).toBeCloseTo(0.3, 6);
    expect(away!.urgency).toBe("far");

    const through = distanceToFill({
      action: "BUY",
      limitPrice: 1.5,
      lastPrice: 1.4,
    });
    expect(through!.urgency).toBe("through");
    expect(through!.delta).toBeCloseTo(-0.1, 6);
  });

  it("SELL limit: market below limit is away; market at/above is through", () => {
    const away = distanceToFill({
      action: "SELL",
      limitPrice: 2.0,
      lastPrice: 1.7,
    });
    expect(away!.delta).toBeCloseTo(0.3, 6);
    expect(away!.urgency).toBe("far");

    const near = distanceToFill({
      action: "SELL",
      limitPrice: 2.0,
      lastPrice: 1.98,
    });
    expect(near!.urgency).toBe("near");
  });

  it("returns null without prices", () => {
    expect(
      distanceToFill({ action: "BUY", limitPrice: null, lastPrice: 1 }),
    ).toBeNull();
    expect(
      distanceToFill({ action: "BUY", limitPrice: 1, lastPrice: null }),
    ).toBeNull();
  });
});

describe("resolveOrderIntent", () => {
  it("SELL against held LONG option is CLOSE", () => {
    const order = makeOrder({ action: "SELL", totalQuantity: 5 });
    const intent = resolveOrderIntent(order, [
      makeOptPosition("AAPL", "LONG", 200, "Call"),
    ]);
    expect(intent).toBe("CLOSE");
  });

  it("BUY against held SHORT option is CLOSE", () => {
    const order = makeOrder({ action: "BUY", totalQuantity: 5 });
    const intent = resolveOrderIntent(order, [
      makeOptPosition("AAPL", "SHORT", 200, "Call"),
    ]);
    expect(intent).toBe("CLOSE");
  });

  it("BUY with no portfolio match is OPEN", () => {
    expect(resolveOrderIntent(makeOrder({ action: "BUY" }), [])).toBe("OPEN");
  });

  it("stock SELL against long stock is CLOSE", () => {
    const order = makeOrder({
      action: "SELL",
      contract: {
        conId: 9,
        symbol: "AAPL",
        secType: "STK",
        strike: null,
        right: null,
        expiry: null,
      },
    });
    expect(resolveOrderIntent(order, [makeStockPosition("AAPL", "LONG")])).toBe(
      "CLOSE",
    );
  });

  it("an order larger than the held option quantity is OPEN, not a riskless close", () => {
    const held = makeOptPosition("AAPL", "LONG", 200, "Call");
    held.legs[0].contracts = 5;
    expect(resolveOrderIntent(makeOrder({ action: "SELL", totalQuantity: 10 }), [held])).toBe("OPEN");
  });

  it("finds stock collateral inside a covered-call position", () => {
    const coveredCall = makeStockPosition("AAPL", "LONG", 100);
    coveredCall.structure = "Covered Call";
    coveredCall.legs.push({
      type: "Call",
      strike: 200,
      direction: "SHORT",
      contracts: 1,
      entry_cost: -100,
      avg_cost: 100,
      market_price: 1,
      market_value: -100,
    });
    const sell = makeOrder({
      action: "SELL",
      totalQuantity: 100,
      contract: { conId: 9, symbol: "AAPL", secType: "STK", strike: null, right: null, expiry: null },
    });
    expect(resolveOrderIntent(sell, [coveredCall])).toBe("CLOSE");
  });
});

describe("cardToneForIntent", () => {
  it("CLOSE is default (not action-colored)", () => {
    expect(cardToneForIntent("CLOSE", "SELL")).toBe("default");
    expect(cardToneForIntent("CLOSE", "BUY")).toBe("default");
  });

  it("OPEN BUY positive, OPEN SELL negative", () => {
    expect(cardToneForIntent("OPEN", "BUY")).toBe("positive");
    expect(cardToneForIntent("OPEN", "SELL")).toBe("negative");
  });
});

describe("summarizeOpenOrders", () => {
  it("counts working, partial, and open total", () => {
    const summary = summarizeOpenOrders([
      makeOrder({ filled: 0, remaining: 10, totalQuantity: 10, status: "Submitted" }),
      makeOrder({
        permId: 2,
        filled: 4,
        remaining: 6,
        totalQuantity: 10,
        status: "Submitted",
      }),
    ]);
    expect(summary.openCount).toBe(2);
    expect(summary.partialCount).toBe(1);
    expect(summary.workingCount).toBe(1);
  });
});
