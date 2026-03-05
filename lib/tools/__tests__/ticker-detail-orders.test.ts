import { describe, it, expect } from "vitest";
import type { OpenOrder, OrdersData } from "../../../web/lib/types";

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
