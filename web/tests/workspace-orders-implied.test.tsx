/**
 * @vitest-environment jsdom
 *
 * Component test: WorkspaceSections orders table renders the "Implied"
 * column for a single OPT and a BAG combo.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import WorkspaceSections, { groupExecutedOrders, closedGroupReturnPct } from "../components/WorkspaceSections";
import { bsCall, bsPut } from "../lib/blackScholes";
import { yearsToExpiry } from "../lib/impliedValue";
import type { ExecutedOrder, OrdersData, PortfolioData, PriceData } from "../lib/types";

// Lighten the render: stub out interactive children that pull contexts we don't care about.
vi.mock("../components/TickerLink", () => ({
  default: (props: { ticker: string }) => React.createElement("span", null, props.ticker),
}));

vi.mock("../components/CancelOrderDialog", () => ({ default: () => null }));
vi.mock("../components/ModifyOrderModal", () => ({ default: () => null }));
vi.mock("../components/PerformancePanel", () => ({ default: () => null }));
vi.mock("../components/RegimePanel", () => ({ default: () => null }));
vi.mock("../components/CtaPage", () => ({ default: () => null }));
vi.mock("../components/TickerWorkspace", () => ({ default: () => null }));

vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({
    pendingCancels: new Map(),
    pendingModifies: new Map(),
    cancelledOrders: [],
    requestCancel: vi.fn(),
    requestModify: vi.fn(),
    clearCancelled: vi.fn(),
  }),
  OrderActionsProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@/lib/useBlotter", () => ({
  useBlotter: () => ({ data: null, loading: false, error: null, syncing: false, syncNow: vi.fn() }),
}));

vi.mock("@/lib/useJournal", () => ({
  useJournal: () => ({
    data: { trades: [] },
    loading: false,
    error: null,
    syncWithIB: vi.fn(),
    syncing: false,
    lastSyncResult: null,
  }),
}));

const NOW = new Date();
const expiry = "2099-05-01";

function pd(over: Partial<PriceData>): PriceData {
  return {
    symbol: "X",
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
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
    timestamp: NOW.toISOString(),
    ...over,
  };
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function enableAllImpliedOrderColumns() {
  window.localStorage.setItem(
    "radon:columns:orders-open",
    JSON.stringify({ implied: true, implied_mv: true }),
  );
}

describe("WorkspaceSections orders — Implied column", () => {
  it("uses the portfolio pricing definition for a BAG last price", () => {
    const matchingPosition: PortfolioData["positions"][number] = {
      id: 1,
      ticker: "SMH",
      structure: "Bull Put Spread $545.0/$550.0",
      structure_type: "Bull Put Spread",
      risk_profile: "defined",
      expiry: "2026-09-18",
      contracts: 150,
      direction: "COMBO",
      entry_cost: -34_200,
      max_risk: 40_800,
      market_value: -25_950,
      legs: [
        {
          con_id: 545,
          direction: "LONG",
          contracts: 150,
          type: "Put",
          strike: 545,
          entry_cost: 187_050,
          avg_cost: 1_247,
          market_price: 9.6,
          market_value: 144_000,
        },
        {
          con_id: 550,
          direction: "SHORT",
          contracts: 150,
          type: "Put",
          strike: 550,
          entry_cost: 221_250,
          avg_cost: 1_475,
          market_price: 11.33,
          market_price_is_calculated: true,
          market_value: 169_950,
        },
      ],
      ib_daily_pnl: 289,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-08-01",
    };
    const supersetDecoy: PortfolioData["positions"][number] = {
      ...matchingPosition,
      id: 2,
      structure: "Three-leg SMH position",
      legs: [
        { ...matchingPosition.legs[0], market_price: 8 },
        { ...matchingPosition.legs[1], market_price: 12 },
        {
          con_id: 600,
          direction: "LONG",
          contracts: 150,
          type: "Call",
          strike: 600,
          entry_cost: 15_000,
          avg_cost: 100,
          market_price: 1,
          market_value: 15_000,
        },
      ],
    };
    const portfolio: PortfolioData = {
      bankroll: 100_000,
      peak_value: 100_000,
      last_sync: NOW.toISOString(),
      total_deployed_pct: 0,
      total_deployed_dollars: 0,
      remaining_capacity_pct: 100,
      position_count: 2,
      defined_risk_count: 2,
      undefined_risk_count: 0,
      avg_kelly_optimal: null,
      positions: [supersetDecoy, matchingPosition],
    };
    const orders: OrdersData = {
      last_sync: NOW.toISOString(),
      open_count: 1,
      executed_count: 0,
      executed_orders: [],
      open_orders: [
        {
          orderId: 71,
          permId: 7101,
          symbol: "SMH Spread",
          contract: {
            conId: 0,
            symbol: "SMH",
            secType: "BAG",
            strike: null,
            right: null,
            expiry: null,
            comboLegs: [
              { conId: 545, ratio: 1, action: "BUY", symbol: "SMH", strike: 545, right: "P", expiry: "2026-09-18" },
              { conId: 550, ratio: 1, action: "SELL", symbol: "SMH", strike: 550, right: "P", expiry: "2026-09-18" },
            ],
          },
          action: "SELL",
          orderType: "LMT",
          totalQuantity: 150,
          limitPrice: -1.95,
          auxPrice: null,
          status: "Submitted",
          filled: 0,
          remaining: 150,
          avgFillPrice: null,
          tif: "GTC",
        },
      ],
    };
    const prices: Record<string, PriceData> = {
      SMH_20260918_545_P: pd({
        symbol: "SMH_20260918_545_P",
        last: 9.6,
        bid: 9.16,
        ask: 9.6,
      }),
    };

    render(
      React.createElement(WorkspaceSections, {
        section: "orders",
        orders,
        prices,
        portfolio,
      }),
    );

    const table = screen.getByTestId("open-orders-table");
    const row = within(table).getByText("SMH").closest("tr");
    expect(row?.textContent).toContain("C$-1.73");
    expect(row?.textContent).toContain("-0.22");
  });

  it("groups executions by durable order identity instead of calendar minute", () => {
    const fill = (execId: string, orderRef: string, time: string): ExecutedOrder => ({
      execId, orderRef, symbol: "SPY", side: "BOT", quantity: 1, avgPrice: 2,
      commission: 0, realizedPNL: null, time, exchange: "SMART",
      contract: { conId: 1, symbol: "SPY", secType: "OPT", strike: 600, right: "C", expiry: "20260821" },
    });
    const groups = groupExecutedOrders([
      fill("a", "intent-a", "2026-08-13T14:30:59Z"),
      fill("b", "intent-b", "2026-08-13T14:31:00Z"),
    ]);
    expect(groups).toHaveLength(2);
  });

  it("classifies a stock SELL carrying realizedPNL as a close and surfaces its P&L", () => {
    const groups = groupExecutedOrders([
      {
        execId: "s1", orderRef: "sell-avgo", symbol: "AVGO", side: "SLD",
        quantity: 1000, avgPrice: 355, commission: 9.91, realizedPNL: 10650,
        time: "2026-09-02T20:24:15Z", exchange: "SMART",
        contract: { conId: 5, symbol: "AVGO", secType: "STK", strike: null, right: null, expiry: null },
      },
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].isClosing).toBe(true);
    expect(groups[0].totalPnL).toBeCloseTo(10650, 2);
    // Return % uses the ×1 stock multiplier: 10650 / (355×1000 − 10650) ≈ 3.09%
    expect(closedGroupReturnPct(groups[0])).toBeCloseTo(3.093, 2);
  });

  it("uses quantity-weighted BAG execution price and rejects incomplete aggregates", () => {
    const bag = (execId: string, quantity: number, avgPrice: number | null): ExecutedOrder => ({
      execId, orderRef: "combo", symbol: "SPY BAG", side: "BOT", quantity, avgPrice,
      commission: 0, realizedPNL: null, time: "2026-08-13T14:30:00Z", exchange: "SMART",
      contract: { conId: 0, symbol: "SPY", secType: "BAG", strike: null, right: null, expiry: null },
    });
    expect(groupExecutedOrders([bag("a", 1, 2), bag("b", 3, 4)])[0].netPrice).toBe(3.5);
    expect(groupExecutedOrders([bag("a", 1, 2), bag("b", 3, null)])[0].netPrice).toBeNull();
  });

  it("hides Implied + Implied MV headers when the orders table contains only STK rows", () => {
    const orders: OrdersData = {
      last_sync: NOW.toISOString(),
      open_count: 1,
      executed_count: 0,
      executed_orders: [],
      open_orders: [
        {
          orderId: 9,
          permId: 9,
          symbol: "TSLA",
          contract: {
            conId: 1,
            symbol: "TSLA",
            secType: "STK",
            strike: null,
            right: null,
            expiry: null,
          },
          action: "BUY",
          orderType: "LMT",
          totalQuantity: 100,
          limitPrice: 250,
          auxPrice: null,
          status: "Submitted",
          filled: 0,
          remaining: 100,
          avgFillPrice: null,
          tif: "DAY",
        },
      ],
    };
    render(
      React.createElement(WorkspaceSections, {
        section: "orders",
        orders,
        prices: {},
        portfolio: null,
      }),
    );
    expect(screen.queryByText("Implied")).toBeNull();
    expect(screen.queryByText("Implied MV")).toBeNull();
  });

  it("renders 'Implied MV' header in the orders table when toggled on", () => {
    enableAllImpliedOrderColumns();
    const orders: OrdersData = {
      last_sync: NOW.toISOString(),
      open_count: 1,
      executed_count: 0,
      executed_orders: [],
      open_orders: [
        {
          orderId: 1,
          permId: 1,
          symbol: "AMD",
          contract: {
            conId: 1,
            symbol: "AMD",
            secType: "OPT",
            strike: 295,
            right: "P",
            expiry: expiry.replace(/-/g, ""),
          },
          action: "BUY",
          orderType: "LMT",
          totalQuantity: 1,
          limitPrice: 3.0,
          auxPrice: null,
          status: "Submitted",
          filled: 0,
          remaining: 1,
          avgFillPrice: null,
          tif: "DAY",
        },
      ],
    };
    render(
      React.createElement(WorkspaceSections, {
        section: "orders",
        orders,
        prices: {},
        portfolio: null,
      }),
    );
    // "Implied MV" appears as both <th> and as a checkbox label in the toggle menu.
    expect(screen.getAllByText("Implied MV").length).toBeGreaterThan(0);
  });

  it("hides Implied by default even when OPT orders are present", () => {
    const orders: OrdersData = {
      last_sync: NOW.toISOString(),
      open_count: 1,
      executed_count: 0,
      executed_orders: [],
      open_orders: [
        {
          orderId: 1,
          permId: 1,
          symbol: "AMD",
          contract: {
            conId: 1,
            symbol: "AMD",
            secType: "OPT",
            strike: 295,
            right: "P",
            expiry: expiry.replace(/-/g, ""),
          },
          action: "BUY",
          orderType: "LMT",
          totalQuantity: 1,
          limitPrice: 3.0,
          auxPrice: null,
          status: "Submitted",
          filled: 0,
          remaining: 1,
          avgFillPrice: null,
          tif: "DAY",
        },
      ],
    };
    render(
      React.createElement(WorkspaceSections, {
        section: "orders",
        orders,
        prices: {},
        portfolio: null,
      }),
    );
    // Column toggle still lists the option; table header is off by default.
    const table = document.querySelector('[data-testid="open-orders-table"]');
    expect(table).not.toBeNull();
    expect(within(table as HTMLElement).queryByText("Implied")).toBeNull();
  });

  it("renders 'Implied' header and BS-derived value for a single OPT order", () => {
    enableAllImpliedOrderColumns();
    const sigma = 0.45;
    const spot = 280;
    const orders: OrdersData = {
      last_sync: NOW.toISOString(),
      open_count: 1,
      executed_count: 0,
      executed_orders: [],
      open_orders: [
        {
          orderId: 99,
          permId: 99,
          symbol: "AMD",
          contract: {
            conId: 1,
            symbol: "AMD",
            secType: "OPT",
            strike: 295,
            right: "P",
            expiry: expiry.replace(/-/g, ""),
          },
          action: "BUY",
          orderType: "LMT",
          totalQuantity: 1,
          limitPrice: 3.0,
          auxPrice: null,
          status: "Submitted",
          filled: 0,
          remaining: 1,
          avgFillPrice: null,
          tif: "DAY",
        },
      ],
    };

    const prices: Record<string, PriceData> = {
      AMD: pd({ last: spot }),
      [`AMD_${expiry.replace(/-/g, "")}_295_P`]: pd({ impliedVol: sigma }),
    };

    render(
      React.createElement(WorkspaceSections, {
        section: "orders",
        orders,
        prices,
        portfolio: null,
      }),
    );

    expect(screen.getByText("Implied")).toBeTruthy();

    const T = yearsToExpiry(expiry, new Date())!;
    const expected = bsPut(spot, 295, T, 0, sigma).toFixed(2);

    const table = screen.getByText("Implied").closest("table");
    expect(table).not.toBeNull();
    const rowText = Array.from(table!.querySelectorAll("tbody tr"))
      .map((r) => r.textContent ?? "")
      .join("\n");
    expect(rowText).toContain(expected);
  });

  it("renders signed combo Implied for a BAG (vertical call spread)", () => {
    enableAllImpliedOrderColumns();
    const sigma = 0.3;
    const expiryV = "20990619";
    const spot = 105;

    const orders: OrdersData = {
      last_sync: NOW.toISOString(),
      open_count: 2,
      executed_count: 0,
      executed_orders: [],
      open_orders: [
        {
          orderId: 1,
          permId: 1,
          symbol: "AMD",
          contract: {
            conId: 1,
            symbol: "AMD",
            secType: "OPT",
            strike: 100,
            right: "C",
            expiry: expiryV,
          },
          action: "BUY",
          orderType: "LMT",
          totalQuantity: 5,
          limitPrice: 5.0,
          auxPrice: null,
          status: "Submitted",
          filled: 0,
          remaining: 5,
          avgFillPrice: null,
          tif: "DAY",
        },
        {
          orderId: 2,
          permId: 2,
          symbol: "AMD",
          contract: {
            conId: 2,
            symbol: "AMD",
            secType: "OPT",
            strike: 110,
            right: "C",
            expiry: expiryV,
          },
          action: "SELL",
          orderType: "LMT",
          totalQuantity: 5,
          limitPrice: 1.0,
          auxPrice: null,
          status: "Submitted",
          filled: 0,
          remaining: 5,
          avgFillPrice: null,
          tif: "DAY",
        },
      ],
    };

    const prices: Record<string, PriceData> = {
      AMD: pd({ last: spot }),
      [`AMD_${expiryV}_100_C`]: pd({ impliedVol: sigma }),
      [`AMD_${expiryV}_110_C`]: pd({ impliedVol: sigma }),
    };

    render(
      React.createElement(WorkspaceSections, {
        section: "orders",
        orders,
        prices,
        portfolio: null,
      }),
    );

    const T = yearsToExpiry("2099-06-19", new Date())!;
    const expected =
      Math.round((bsCall(spot, 100, T, 0, sigma) - bsCall(spot, 110, T, 0, sigma)) * 100) / 100;

    const table = screen.getByText("Implied").closest("table");
    expect(table).not.toBeNull();
    const rowsText = Array.from(table!.querySelectorAll("tbody tr"))
      .map((r) => r.textContent ?? "")
      .join("\n");
    // BAG combo row aggregates legs; the implied value should appear.
    expect(rowsText).toContain(expected.toFixed(2));
  });
});
