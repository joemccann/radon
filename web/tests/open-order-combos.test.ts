import { describe, expect, it } from "vitest";
import type { ExecutedOrder, OpenOrder, PortfolioPosition } from "../lib/types";
import { buildExecutedGroupDescription, buildOpenOrderDisplayRows, resolveOpenOrderComboPrice } from "../lib/openOrderCombos";
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
    orderRef: overrides.orderRef === undefined ? "combo-test" : overrides.orderRef,
    ocaGroup: overrides.ocaGroup,
    parentId: overrides.parentId,
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

function makePortfolioPosition(overrides: Partial<PortfolioPosition> = {}): PortfolioPosition {
  return {
    id: overrides.id ?? 9001,
    ticker: overrides.ticker ?? "AAPL",
    structure: overrides.structure ?? "Risk Reversal",
    structure_type: overrides.structure_type ?? "Risk Reversal",
    risk_profile: overrides.risk_profile ?? "",
    expiry: overrides.expiry ?? "2026-04-17",
    contracts: overrides.contracts ?? 10,
    direction: overrides.direction ?? "COMBO",
    entry_cost: overrides.entry_cost ?? 0,
    max_risk: overrides.max_risk ?? null,
    market_value: overrides.market_value ?? 0,
    legs: overrides.legs ?? [
      { direction: "SHORT", contracts: 10, type: "Put", strike: 85, entry_cost: 0, avg_cost: 0, market_price: 1.2, market_value: 1200 },
      { direction: "LONG", contracts: 10, type: "Call", strike: 90, entry_cost: 0, avg_cost: 0, market_price: 0.9, market_value: 900 },
    ],
    ib_daily_pnl: overrides.ib_daily_pnl ?? null,
    kelly_optimal: overrides.kelly_optimal ?? null,
    target: overrides.target ?? null,
    stop: overrides.stop ?? null,
    entry_date: overrides.entry_date ?? "2026-03-17",
  };
}

function makeExecutedFill(overrides: Partial<ExecutedOrder> & { contract?: Partial<ExecutedOrder["contract"]> } = {}): ExecutedOrder {
  const { contract: contractOverrides, ...rest } = overrides;
  return {
    execId: "exec-1",
    symbol: "AAOI",
    contract: {
      conId: 1001,
      symbol: "AAOI",
      secType: "OPT",
      strike: 90,
      right: "C",
      expiry: "2026-04-17",
      ...contractOverrides,
    },
    side: "BOT",
    quantity: 25,
    avgPrice: 0.5,
    commission: -1.23,
    realizedPNL: 1250,
    time: "2026-03-17T15:40:21+00:00",
    exchange: "SMART",
    ...rest,
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

  it("keeps short put / long call orientation for a closing risk reversal", () => {
    const rows = buildOpenOrderDisplayRows([
      makeOrder({ orderId: 1, permId: 101, action: "BUY", right: "P", strike: 85, expiry: "2026-04-17", totalQuantity: 10 }),
      makeOrder({ orderId: 2, permId: 102, action: "SELL", right: "C", strike: 90, expiry: "2026-04-17", totalQuantity: 10 }),
    ]);

    expect(rows).toHaveLength(1);
    const row = rows[0];
    expect(row.kind).toBe("combo");
    if (row.kind !== "combo") return;

    expect(row.structure).toBe("Risk Reversal");
    expect(row.summary).toContain("Short Put 85");
    expect(row.summary).toContain("Long Call 90");
    expect(row.summary.indexOf("Short Put 85")).toBeLessThan(row.summary.indexOf("Long Call 90"));
  });

  it("prefers portfolio leg direction when a risk reversal is closing an existing position", () => {
    const rows = buildOpenOrderDisplayRows([
      makeOrder({ orderId: 1, permId: 101, action: "SELL", right: "C", strike: 90, expiry: "2026-04-17", totalQuantity: 10 }),
      makeOrder({ orderId: 2, permId: 102, action: "BUY", right: "P", strike: 85, expiry: "2026-04-17", totalQuantity: 10 }),
    ], [
      makePortfolioPosition({
        ticker: "AAPL",
        legs: [
          { direction: "SHORT", contracts: 10, type: "Put", strike: 85, entry_cost: 0, avg_cost: 0, market_price: 1.2, market_value: 1200 },
          { direction: "LONG", contracts: 10, type: "Call", strike: 90, entry_cost: 0, avg_cost: 0, market_price: 0.9, market_value: 900 },
        ],
      }),
    ]);

    expect(rows).toHaveLength(1);
    const row = rows[0];
    expect(row.kind).toBe("combo");
    if (row.kind !== "combo") return;
    expect(row.summary).toContain("Short Put 85");
    expect(row.summary).toContain("Long Call 90");
    expect(row.summary.indexOf("Short Put 85")).toBeLessThan(row.summary.indexOf("Long Call 90"));
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

    expect(rows).toHaveLength(2);
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
    // Short put financing a bull call spread — named since 2026-07-21
    // (was the generic "3-Leg Combo").
    expect(row.structure).toBe("Risk Reversal Call Spread");
  });

  it("keeps same-shape orders as non-actionable singles without explicit broker correlation", () => {
    const rows = buildOpenOrderDisplayRows([
      makeOrder({ orderId: 41, action: "SELL", right: "P", strike: 150, orderRef: null }),
      makeOrder({ orderId: 42, action: "BUY", right: "C", strike: 165, orderRef: null }),
    ]);

    expect(rows).toHaveLength(2);
    expect(rows.every((row) => row.kind === "single")).toBe(true);
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
    expect(net).toBeCloseTo(-3, 4);
  });

  it("weights legs by relative quantity for ratio combos", () => {
    const shortPut = makeOrder({ orderId: 1, action: "SELL", right: "P", strike: 150, totalQuantity: 25 });
    const longCall = makeOrder({ orderId: 2, action: "BUY", right: "C", strike: 165, totalQuantity: 50 });
    const prices: Record<string, PriceData> = {
      AAPL_20260417_150_P: makePrice({ symbol: "AAPL_20260417_150_P", bid: 4.5, ask: 5.5 }),
      AAPL_20260417_165_C: makePrice({ symbol: "AAPL_20260417_165_C", bid: 2.0, ask: 2.2 }),
    };

    const net = resolveOpenOrderComboPrice([shortPut, longCall], prices);
    expect(net).toBeCloseTo(-0.8, 4);
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

describe("buildExecutedGroupDescription", () => {
  it("shows closing risk reversal legs in original direction", () => {
    const fills = [
      makeExecutedFill({
        symbol: "AAOI",
        side: "BOT",
        contract: { conId: 111, symbol: "AAOI", secType: "OPT", strike: 85, right: "P", expiry: "2026-04-17" },
      }),
      makeExecutedFill({
        execId: "exec-2",
        side: "SLD",
        avgPrice: 0.6,
        contract: { conId: 222, symbol: "AAOI", secType: "OPT", strike: 90, right: "C", expiry: "2026-04-17" },
      }),
    ];

    const description = buildExecutedGroupDescription(fills, true);
    expect(description).toContain("Closed AAOI Risk Reversal");
    expect(description).toContain("Short $85 Put");
    expect(description).toContain("Long $90 Call");
    expect(description.indexOf("Short $85 Put")).toBeLessThan(description.indexOf("Long $90 Call"));
  });

  it("uses portfolio direction when available for closing risk reversal descriptions", () => {
    const fills = [
      makeExecutedFill({
        symbol: "AAOI",
        side: "BUY",
        contract: { conId: 111, symbol: "AAOI", secType: "OPT", strike: 85, right: "P", expiry: "2026-04-17" },
      }),
      makeExecutedFill({
        execId: "exec-2",
        side: "SELL",
        avgPrice: 0.6,
        contract: { conId: 222, symbol: "AAOI", secType: "OPT", strike: 90, right: "C", expiry: "2026-04-17" },
      }),
    ];

    const description = buildExecutedGroupDescription(fills, true, [
      {
        id: 1,
        ticker: "AAOI",
        structure: "Risk Reversal",
        structure_type: "Risk Reversal",
        risk_profile: "",
        expiry: "2026-04-17",
        contracts: 25,
        direction: "",
        entry_cost: 0,
        max_risk: null,
        market_value: 0,
        legs: [
          { direction: "SHORT", contracts: 25, type: "Put", strike: 85, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
          { direction: "LONG", contracts: 25, type: "Call", strike: 90, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
        ],
        ib_daily_pnl: null,
        kelly_optimal: null,
        target: null,
        stop: null,
        entry_date: "2026-03-17",
      },
    ]);

    expect(description).toContain("Closed AAOI Risk Reversal");
    expect(description).toContain("Short $85 Put");
    expect(description).toContain("Long $90 Call");
    expect(description.indexOf("Short $85 Put")).toBeLessThan(description.indexOf("Long $90 Call"));
  });

  it("uses explicit closing fill sides when they are present", () => {
    const fills = [
      makeExecutedFill({
        symbol: "AAOI",
        side: "SLD",
        contract: { conId: 111, symbol: "AAOI", secType: "OPT", strike: 85, right: "P", expiry: "2026-04-17" },
      }),
      makeExecutedFill({
        execId: "exec-2",
        side: "BOT",
        avgPrice: 0.6,
        contract: { conId: 222, symbol: "AAOI", secType: "OPT", strike: 90, right: "C", expiry: "2026-04-17" },
      }),
    ];

    const description = buildExecutedGroupDescription(fills, true, [
      {
        id: 1,
        ticker: "AAOI",
        structure: "Risk Reversal",
        structure_type: "Risk Reversal",
        risk_profile: "",
        expiry: "2026-04-17",
        contracts: 25,
        direction: "",
        entry_cost: 0,
        max_risk: null,
        market_value: 0,
        legs: [
          { direction: "SHORT", contracts: 25, type: "Put", strike: 85, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
          { direction: "LONG", contracts: 25, type: "Call", strike: 90, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
        ],
        ib_daily_pnl: null,
        kelly_optimal: null,
        target: null,
        stop: null,
        entry_date: "2026-03-17",
      },
    ]);

    expect(description).toContain("Closed AAOI Risk Reversal");
    expect(description).toContain("Short $90 Call");
    expect(description).toContain("Long $85 Put");
  });
});
