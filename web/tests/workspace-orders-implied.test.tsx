/**
 * @vitest-environment jsdom
 *
 * Component test: WorkspaceSections orders table renders the "Implied"
 * column for a single OPT and a BAG combo.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import WorkspaceSections from "../components/WorkspaceSections";
import { bsCall, bsPut } from "../lib/blackScholes";
import { yearsToExpiry } from "../lib/impliedValue";
import type { OrdersData, PriceData } from "../lib/types";

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
  // nothing yet
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("WorkspaceSections orders — Implied column", () => {
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

  it("renders 'Implied MV' header in the orders table", () => {
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
    expect(screen.getByText("Implied MV")).toBeTruthy();
  });

  it("renders 'Implied' header and BS-derived value for a single OPT order", () => {
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
