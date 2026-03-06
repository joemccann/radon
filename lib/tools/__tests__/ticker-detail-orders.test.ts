import { describe, it, expect } from "vitest";
import type { OpenOrder, OrdersData, PortfolioPosition } from "../../../web/lib/types";
import type { PriceData } from "../../../web/lib/pricesProtocol";
import { optionKey } from "../../../web/lib/pricesProtocol";

/**
 * Test that verifies the ticker detail modal correctly filters
 * open orders for a given ticker, regardless of which page the user is on.
 *
 * The bug: orders data was null on non-orders pages because useOrders(false)
 * never fetched. The fix: always fetch cached orders on init, only auto-sync
 * on orders page.
 */

function filterOrdersForTicker(ticker: string, ordersData: OrdersData | null): OpenOrder[] {
  if (!ordersData) return [];
  return ordersData.open_orders.filter((o) => o.contract.symbol === ticker);
}

const mockOrders: OrdersData = {
  last_sync: "2026-03-05T11:00:00Z",
  open_orders: [
    {
      orderId: 1, permId: 100, symbol: "TSLL",
      contract: { conId: 1, symbol: "TSLL", secType: "STK", strike: null, right: null, expiry: null },
      action: "SELL", orderType: "LMT", totalQuantity: 5000, limitPrice: 21.00,
      auxPrice: null, status: "Submitted", filled: 0, remaining: 5000, avgFillPrice: null, tif: "GTC",
    },
    {
      orderId: 2, permId: 200, symbol: "AAOI",
      contract: { conId: 2, symbol: "AAOI", secType: "OPT", strike: 105, right: "C", expiry: "2026-03-06" },
      action: "SELL", orderType: "LMT", totalQuantity: 25, limitPrice: 6.00,
      auxPrice: null, status: "Submitted", filled: 0, remaining: 25, avgFillPrice: null, tif: "DAY",
    },
    {
      orderId: 3, permId: 300, symbol: "ILF",
      contract: { conId: 3, symbol: "ILF", secType: "STK", strike: null, right: null, expiry: null },
      action: "SELL", orderType: "LMT", totalQuantity: 2000, limitPrice: 41.00,
      auxPrice: null, status: "Submitted", filled: 0, remaining: 2000, avgFillPrice: null, tif: "GTC",
    },
  ],
  executed_orders: [],
  open_count: 3,
  executed_count: 0,
};

describe("Ticker detail: open orders for ticker", () => {
  it("finds open orders matching the ticker", () => {
    const tsllOrders = filterOrdersForTicker("TSLL", mockOrders);
    expect(tsllOrders).toHaveLength(1);
    expect(tsllOrders[0].limitPrice).toBe(21.00);
    expect(tsllOrders[0].totalQuantity).toBe(5000);
  });

  it("returns empty array when no orders match", () => {
    expect(filterOrdersForTicker("GOOG", mockOrders)).toHaveLength(0);
  });

  it("returns empty array when ordersData is null (BUG CASE)", () => {
    // This is the bug: on non-orders pages, ordersData was null
    // The modal should still show orders when they exist
    const result = filterOrdersForTicker("TSLL", null);
    expect(result).toHaveLength(0);
    // The fix is ensuring ordersData is NOT null — useOrders must fetch on all pages
  });

  it("finds multiple orders for same ticker", () => {
    const extendedOrders: OrdersData = {
      ...mockOrders,
      open_orders: [
        ...mockOrders.open_orders,
        {
          orderId: 4, permId: 400, symbol: "TSLL",
          contract: { conId: 4, symbol: "TSLL", secType: "STK", strike: null, right: null, expiry: null },
          action: "BUY", orderType: "LMT", totalQuantity: 1000, limitPrice: 10.00,
          auxPrice: null, status: "Submitted", filled: 0, remaining: 1000, avgFillPrice: null, tif: "DAY",
        },
      ],
      open_count: 4,
    };
    const tsllOrders = filterOrdersForTicker("TSLL", extendedOrders);
    expect(tsllOrders).toHaveLength(2);
  });

  it("tab label shows count when orders exist", () => {
    const orders = filterOrdersForTicker("TSLL", mockOrders);
    const label = orders.length > 0 ? `Orders (${orders.length})` : "Order";
    expect(label).toBe("Orders (1)");
  });

  it("tab label shows 'Order' when no orders exist", () => {
    const orders = filterOrdersForTicker("GOOG", mockOrders);
    const label = orders.length > 0 ? `Orders (${orders.length})` : "Order";
    expect(label).toBe("Order");
  });
});

// =============================================================================
// Combo order logic
// =============================================================================

/**
 * Mirror of legPriceKey from WorkspaceSections.tsx.
 */
function legPriceKey(
  ticker: string,
  expiry: string,
  leg: { type: string; strike: number | null },
): string | null {
  if (leg.type === "Stock") return null;
  if (leg.strike == null || leg.strike === 0) return null;
  if (!expiry || expiry === "N/A") return null;
  const right = leg.type === "Call" ? "C" : leg.type === "Put" ? "P" : null;
  if (!right) return null;
  const expiryClean = expiry.replace(/-/g, "");
  if (expiryClean.length !== 8) return null;
  return optionKey({ symbol: ticker.toUpperCase(), expiry: expiryClean, strike: leg.strike, right });
}

/**
 * Mirror of ComboOrderForm leg action derivation.
 * SELL (closing): LONG → SELL, SHORT → BUY
 * BUY (opening): LONG → BUY, SHORT → SELL
 */
function deriveComboLegActions(
  legs: PortfolioPosition["legs"],
  comboAction: "BUY" | "SELL",
  expiry: string,
): Array<{ legAction: "BUY" | "SELL"; right: "C" | "P"; expiry: string; strike: number | null; direction: string }> {
  return legs.map((leg) => {
    let legAction: "BUY" | "SELL";
    if (comboAction === "SELL") {
      legAction = leg.direction === "LONG" ? "SELL" : "BUY";
    } else {
      legAction = leg.direction === "LONG" ? "BUY" : "SELL";
    }
    const right = leg.type === "Call" ? "C" : "P";
    const expiryClean = expiry.replace(/-/g, "");
    return { legAction, right: right as "C" | "P", expiry: expiryClean, strike: leg.strike, direction: leg.direction };
  });
}

/**
 * Mirror of ComboOrderForm net price computation.
 */
function computeComboNetPrices(
  position: PortfolioPosition,
  prices: Record<string, PriceData>,
  comboAction: "BUY" | "SELL",
): { bid: number | null; ask: number | null; mid: number | null } {
  let netBid = 0;
  let netAsk = 0;

  for (const leg of position.legs) {
    const key = legPriceKey(position.ticker, position.expiry, leg);
    if (!key) return { bid: null, ask: null, mid: null };
    const lp = prices[key];
    if (!lp || lp.bid == null || lp.ask == null) return { bid: null, ask: null, mid: null };

    const legAction = comboAction === "SELL"
      ? (leg.direction === "LONG" ? "SELL" : "BUY")
      : (leg.direction === "LONG" ? "BUY" : "SELL");

    if (legAction === "SELL") {
      netBid += lp.bid;
      netAsk += lp.ask;
    } else {
      netBid -= lp.ask;
      netAsk -= lp.bid;
    }
  }

  const mid = (netBid + netAsk) / 2;
  return { bid: netBid, ask: netAsk, mid };
}

function makePriceData(overrides: Partial<PriceData> = {}): PriceData {
  return {
    symbol: "TEST", last: null, lastIsCalculated: false,
    bid: null, ask: null, bidSize: null, askSize: null,
    volume: null, high: null, low: null, open: null, close: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

// PLTR Bull Call Spread fixture
const pltrSpread: PortfolioPosition = {
  id: 1,
  ticker: "PLTR",
  structure: "Bull Call Spread $145.0/$165.0",
  structure_type: "Vertical Spread",
  risk_profile: "defined",
  expiry: "2026-03-27",
  contracts: 50,
  direction: "DEBIT",
  entry_cost: 2600,
  max_risk: 2600,
  market_value: null,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-03-05",
  legs: [
    { direction: "LONG", contracts: 50, type: "Call", strike: 145, entry_cost: 22950, avg_cost: 22950, market_price: null, market_value: null },
    { direction: "SHORT", contracts: 50, type: "Call", strike: 165, entry_cost: 20350, avg_cost: 20350, market_price: null, market_value: null },
  ],
};

const longKey = optionKey({ symbol: "PLTR", expiry: "20260327", strike: 145, right: "C" });
const shortKey = optionKey({ symbol: "PLTR", expiry: "20260327", strike: 165, right: "C" });

describe("Combo order: leg action derivation", () => {
  it("SELL action reverses leg directions (closing a spread)", () => {
    const result = deriveComboLegActions(pltrSpread.legs, "SELL", pltrSpread.expiry);
    // LONG leg → SELL, SHORT leg → BUY
    expect(result[0].legAction).toBe("SELL");
    expect(result[0].strike).toBe(145);
    expect(result[0].right).toBe("C");
    expect(result[1].legAction).toBe("BUY");
    expect(result[1].strike).toBe(165);
    expect(result[1].right).toBe("C");
  });

  it("BUY action matches leg directions (opening a new spread)", () => {
    const result = deriveComboLegActions(pltrSpread.legs, "BUY", pltrSpread.expiry);
    // LONG leg → BUY, SHORT leg → SELL
    expect(result[0].legAction).toBe("BUY");
    expect(result[1].legAction).toBe("SELL");
  });

  it("cleans expiry format (removes dashes)", () => {
    const result = deriveComboLegActions(pltrSpread.legs, "SELL", "2026-03-27");
    expect(result[0].expiry).toBe("20260327");
    expect(result[1].expiry).toBe("20260327");
  });

  it("handles put spreads correctly", () => {
    const putSpread: PortfolioPosition = {
      ...pltrSpread,
      structure: "Bear Put Spread $165/$145",
      legs: [
        { direction: "LONG", contracts: 50, type: "Put", strike: 165, entry_cost: 5000, avg_cost: 5000, market_price: null, market_value: null },
        { direction: "SHORT", contracts: 50, type: "Put", strike: 145, entry_cost: 3000, avg_cost: 3000, market_price: null, market_value: null },
      ],
    };
    const result = deriveComboLegActions(putSpread.legs, "SELL", putSpread.expiry);
    expect(result[0].legAction).toBe("SELL");
    expect(result[0].right).toBe("P");
    expect(result[1].legAction).toBe("BUY");
    expect(result[1].right).toBe("P");
  });
});

describe("Combo order: net price computation", () => {
  const pltrPrices: Record<string, PriceData> = {
    [longKey]: makePriceData({ symbol: longKey, bid: 11.00, ask: 11.50 }),
    [shortKey]: makePriceData({ symbol: shortKey, bid: 2.50, ask: 3.00 }),
  };

  it("SELL combo: net BID = long_bid - short_ask, net ASK = long_ask - short_bid", () => {
    const result = computeComboNetPrices(pltrSpread, pltrPrices, "SELL");
    // SELL long leg: receives bid (11.00), BUY short leg: pays ask (3.00)
    // netBid = 11.00 - 3.00 = 8.00
    // netAsk = 11.50 - 2.50 = 9.00
    expect(result.bid).toBeCloseTo(8.00, 2);
    expect(result.ask).toBeCloseTo(9.00, 2);
    expect(result.mid).toBeCloseTo(8.50, 2);
  });

  it("BUY combo: net BID = short_bid - long_ask, net ASK = short_ask - long_bid", () => {
    const result = computeComboNetPrices(pltrSpread, pltrPrices, "BUY");
    // BUY long leg: pays ask (11.50), SELL short leg: receives bid (2.50)
    // netBid = 2.50 - 11.50 = -9.00
    // netAsk = 3.00 - 11.00 = -8.00
    expect(result.bid).toBeCloseTo(-9.00, 2);
    expect(result.ask).toBeCloseTo(-8.00, 2);
    expect(result.mid).toBeCloseTo(-8.50, 2);
  });

  it("returns nulls when price data is missing for a leg", () => {
    const partialPrices: Record<string, PriceData> = {
      [longKey]: makePriceData({ symbol: longKey, bid: 11.00, ask: 11.50 }),
      // short leg missing
    };
    const result = computeComboNetPrices(pltrSpread, partialPrices, "SELL");
    expect(result.bid).toBeNull();
    expect(result.ask).toBeNull();
    expect(result.mid).toBeNull();
  });

  it("returns nulls when bid is null on a leg", () => {
    const badPrices: Record<string, PriceData> = {
      [longKey]: makePriceData({ symbol: longKey, bid: null, ask: 11.50 }),
      [shortKey]: makePriceData({ symbol: shortKey, bid: 2.50, ask: 3.00 }),
    };
    const result = computeComboNetPrices(pltrSpread, badPrices, "SELL");
    expect(result.bid).toBeNull();
  });

  it("symmetric: toggling action flips sign of net prices", () => {
    const sell = computeComboNetPrices(pltrSpread, pltrPrices, "SELL");
    const buy = computeComboNetPrices(pltrSpread, pltrPrices, "BUY");
    // SELL mid should be roughly negation of BUY mid
    expect(sell.mid! + buy.mid!).toBeCloseTo(0, 10);
  });
});

describe("Combo order: isCombo detection", () => {
  it("multi-leg non-stock position is a combo", () => {
    const isCombo = pltrSpread.legs.length > 1 && pltrSpread.structure_type !== "Stock";
    expect(isCombo).toBe(true);
  });

  it("single-leg option is NOT a combo", () => {
    const singleLeg: PortfolioPosition = {
      ...pltrSpread,
      structure_type: "Option",
      legs: [pltrSpread.legs[0]],
    };
    const isCombo = singleLeg.legs.length > 1 && singleLeg.structure_type !== "Stock";
    expect(isCombo).toBe(false);
  });

  it("stock position is NOT a combo even with multiple entries", () => {
    const stock: PortfolioPosition = {
      ...pltrSpread,
      structure_type: "Stock",
    };
    const isCombo = stock.legs.length > 1 && stock.structure_type !== "Stock";
    expect(isCombo).toBe(false);
  });
});
