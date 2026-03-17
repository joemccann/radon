import { describe, expect, it } from "vitest";
import type { OpenOrder } from "../lib/types";
import { buildOpenOrderDisplayRows, resolveOpenOrderComboPrice } from "../lib/openOrderCombos";
import type { PriceData } from "../lib/pricesProtocol";

function makeOrder(overrides: Partial<OpenOrder> & { symbol?: string; right?: string; strike?: number; expiry?: string } = {}): OpenOrder {
  const symbol = overrides.symbol ?? "AAPL";
  const contract: OpenOrder["contract"] = {
    conId: overrides.contract?.conId ?? 1234,
    symbol,
    secType: overrides.contract?.secType ?? "OPT",
    strike: overrides.contract?.strike ?? (overrides.strike ?? 0),
    right: overrides.contract?.right ?? overrides.right ?? "C",
    expiry: overrides.contract?.expiry ?? (overrides.expiry ?? "2026-04-17"),
    ...(overrides.contract ? { comboLegs: overrides.contract.comboLegs } : {}),
  };

  const totalQuantity = overrides.totalQuantity ?? 10;
  return {
    orderId: overrides.orderId ?? 1,
    permId: overrides.permId ?? 1001,
    symbol,
    contract,
    action: overrides.action ?? "BUY",
    orderType: overrides.orderType ?? "LMT",
    totalQuantity,
    limitPrice: overrides.limitPrice ?? null,
    auxPrice: overrides.auxPrice ?? null,
    status: overrides.status ?? "Submitted",
    filled: overrides.filled ?? 0,
    remaining: overrides.remaining ?? totalQuantity,
    avgFillPrice: overrides.avgFillPrice ?? null,
    tif: overrides.tif ?? "DAY",
  };
}

function makeStockOrder(overrides: Partial<OpenOrder> = {}): OpenOrder {
  return {
    ...makeOrder({ ...overrides, totalQuantity: overrides.totalQuantity ?? 20 }),
    contract: {
      conId: overrides.contract?.conId ?? 9999,
      symbol: overrides.contract?.symbol ?? "AAPL",
      secType: "STK",
      strike: null,
      right: null,
      expiry: null,
      ...(overrides.contract ? { comboLegs: overrides.contract.comboLegs } : {}),
    },
    action: overrides.action ?? "BUY",
  };
}

function makePrice(overrides: {
  symbol: string;
  last?: number | null;
  bid?: number | null;
  ask?: number | null;
}): PriceData {
  return {
    symbol: overrides.symbol,
    last: overrides.last ?? null,
    lastIsCalculated: false,
    bid: overrides.bid ?? null,
    ask: overrides.ask ?? null,
    bidSize: 10,
    askSize: 10,
    volume: 100,
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
    timestamp: new Date().toISOString(),
  };
}

describe("buildOpenOrderDisplayRows", () => {
  it("combines short put + long call as a risk reversal", () => {
    const rows = buildOpenOrderDisplayRows([
      makeOrder({ orderId: 1, permId: 101, action: "SELL", right: "P", strike: 150, expiry: "2026-04-17", totalQuantity: 12 }),
      makeOrder({ orderId: 2, permId: 102, action: "BUY", right: "C", strike: 165, expiry: "2026-04-17", totalQuantity: 12 }),
    ]);

    expect(rows).toHaveLength(1);
    const row = rows[0];
    expect(row.kind).toBe("combo");
    if (row.kind !== "combo") return;

    expect(row.structure).toBe("Risk Reversal");
    expect(row.symbol).toBe("AAPL");
    expect(row.totalQuantity).toBe(12);
    expect(row.summary).toContain("Short Put 150");
    expect(row.summary).toContain("Long Call 165");
    expect(row.orders).toHaveLength(2);
  });

  it("does not combine same-direction same-right legs", () => {
    const rows = buildOpenOrderDisplayRows([
      makeOrder({ orderId: 1, action: "BUY", right: "C", strike: 150 }),
      makeOrder({ orderId: 2, action: "BUY", right: "C", strike: 155 }),
    ]);

    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveProperty("kind", "single");
    expect(rows[1]).toHaveProperty("kind", "single");
  });

  it("does not combine different expiries into one combo", () => {
    const rows = buildOpenOrderDisplayRows([
      makeOrder({ orderId: 1, action: "SELL", right: "P", strike: 150, expiry: "2026-04-17" }),
      makeOrder({ orderId: 2, action: "BUY", right: "C", strike: 165, expiry: "2026-05-17" }),
    ]);

    expect(rows).toHaveLength(2);
    expect(rows.map((row) => row.kind)).toEqual(["single", "single"]);
  });

  it("does not combine different quantities into one combo", () => {
    const rows = buildOpenOrderDisplayRows([
      makeOrder({ orderId: 1, action: "SELL", right: "P", strike: 150, totalQuantity: 10 }),
      makeOrder({ orderId: 2, action: "BUY", right: "C", strike: 165, totalQuantity: 11 }),
    ]);

    expect(rows).toHaveLength(2);
    expect(rows.every((row) => row.kind === "single")).toBe(true);
  });

  it("keeps non-option orders as singles", () => {
    const rows = buildOpenOrderDisplayRows([
      makeStockOrder({ orderId: 3, permId: 303, symbol: "AAPL", action: "BUY", totalQuantity: 10 }),
      makeOrder({ orderId: 1, action: "SELL", right: "P", strike: 150, totalQuantity: 10 }),
      makeOrder({ orderId: 2, action: "BUY", right: "C", strike: 165, totalQuantity: 10 }),
    ]);

    expect(rows).toHaveLength(3);
    const comboRows = rows.filter((row) => row.kind === "combo");
    expect(comboRows).toHaveLength(1);
  });

  it("combines any multi-leg matching option set into a combo", () => {
    const rows = buildOpenOrderDisplayRows([
      makeOrder({ orderId: 1, action: "BUY", right: "C", strike: 120, totalQuantity: 5 }),
      makeOrder({ orderId: 2, action: "SELL", right: "C", strike: 140, totalQuantity: 5 }),
      makeOrder({ orderId: 3, action: "SELL", right: "P", strike: 110, totalQuantity: 5 }),
    ]);

    expect(rows).toHaveLength(1);
    const row = rows[0];
    expect(row.kind).toBe("combo");
    if (row.kind !== "combo") return;
    expect(row.structure).toBe("3-Leg Combo");
  });
});

describe("resolveOpenOrderComboPrice", () => {
  it("computes signed net quote from option legs", () => {
    const shortPut = makeOrder({ orderId: 1, action: "SELL", right: "P", strike: 150, totalQuantity: 10 });
    const longCall = makeOrder({ orderId: 2, action: "BUY", right: "C", strike: 165, totalQuantity: 10 });
    const prices: Record<string, PriceData> = {
      AAPL_20260417_150_P: makePrice({ symbol: "AAPL_20260417_150_P", bid: 4.8, ask: 5.2 }),
      AAPL_20260417_165_C: makePrice({ symbol: "AAPL_20260417_165_C", bid: 1.8, ask: 2.2 }),
    };

    const net = resolveOpenOrderComboPrice([shortPut, longCall], prices);
    expect(net).toBeCloseTo(-2.8, 4);
  });

  it("returns null when a leg lacks quote data", () => {
    const shortPut = makeOrder({ orderId: 1, action: "SELL", right: "P", strike: 150, totalQuantity: 10 });
    const longCall = makeOrder({ orderId: 2, action: "BUY", right: "C", strike: 165, totalQuantity: 10 });
    const prices: Record<string, PriceData> = {
      AAPL_20260417_150_P: makePrice({ symbol: "AAPL_20260417_150_P", bid: 4.8, ask: 5.2 }),
      AAPL_20260417_165_C: makePrice({ symbol: "AAPL_20260417_165_C", bid: null, ask: null }),
    };

    const net = resolveOpenOrderComboPrice([shortPut, longCall], prices);
    expect(net).toBeNull();
  });

  it("returns null when mix includes non-option legs", () => {
    const stockOrder = makeStockOrder({ orderId: 3, action: "BUY", totalQuantity: 10 });
    const net = resolveOpenOrderComboPrice([stockOrder], {});
    expect(net).toBeNull();
  });
});
