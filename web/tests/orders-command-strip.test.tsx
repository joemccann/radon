/**
 * @vitest-environment jsdom
 *
 * Desktop /orders command strip + open-order display wiring in WorkspaceSections.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import WorkspaceSections from "../components/WorkspaceSections";
import type { OrdersData, OpenOrder } from "../lib/types";

vi.mock("../components/TickerLink", () => ({
  default: (props: { ticker: string }) => React.createElement("span", null, props.ticker),
}));

vi.mock("../components/Modal", () => ({
  default: ({
    open,
    title,
    children,
  }: {
    open: boolean;
    title: string;
    children: React.ReactNode;
  }) =>
    open
      ? React.createElement(
          "div",
          { role: "dialog", "aria-label": title, "data-testid": "cancel-dialog-modal" },
          React.createElement("h2", null, title),
          children,
        )
      : null,
}));

vi.mock("../components/ModifyOrderModal", () => ({ default: () => null }));
vi.mock("../components/PerformancePanel", () => ({ default: () => null }));
vi.mock("../components/RegimePanel", () => ({ default: () => null }));
vi.mock("../components/CtaPage", () => ({ default: () => null }));
vi.mock("../components/TickerWorkspace", () => ({ default: () => null }));
vi.mock("../components/CashFlowsSection", () => ({
  default: () => React.createElement("div", { id: "orders-cash", "data-testid": "cash-flows-section" }),
}));

const requestCancel = vi.fn(async () => undefined);

vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({
    pendingCancels: new Map(),
    pendingModifies: new Map(),
    cancelledOrders: [],
    requestCancel,
    requestModify: vi.fn(),
    clearCancelled: vi.fn(),
  }),
  OrderActionsProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@/lib/useBlotter", () => ({
  useBlotter: () => ({ data: null, loading: false, error: null, syncing: false, syncNow: vi.fn() }),
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true, width: 1200 }),
}));

vi.mock("@/lib/useRiskFreeRate", () => ({
  useRiskFreeRate: () => 0.04,
}));

function makeOpenOrder(overrides: Partial<OpenOrder> = {}): OpenOrder {
  const totalQuantity = overrides.totalQuantity ?? 10;
  return {
    orderId: overrides.orderId ?? 1,
    permId: overrides.permId ?? 1001,
    symbol: overrides.symbol ?? "AAPL",
    contract: overrides.contract ?? {
      conId: 1,
      symbol: "AAPL",
      secType: "STK",
      strike: null,
      right: null,
      expiry: null,
    },
    action: overrides.action ?? "BUY",
    orderType: overrides.orderType ?? "LMT",
    totalQuantity,
    limitPrice: overrides.limitPrice ?? 100,
    auxPrice: overrides.auxPrice ?? null,
    status: overrides.status ?? "Submitted",
    filled: overrides.filled ?? 0,
    remaining: overrides.remaining ?? totalQuantity,
    avgFillPrice: overrides.avgFillPrice ?? null,
    tif: overrides.tif ?? "DAY",
    ...overrides,
  };
}

const NOW = new Date("2026-07-09T15:00:00.000Z");

beforeEach(() => {
  window.localStorage.clear();
  requestCancel.mockClear();
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("orders command strip", () => {
  it("renders working/partial/fills counts, last sync, and jump anchors", () => {
    const orders: OrdersData = {
      last_sync: new Date(NOW.getTime() - 5 * 60_000).toISOString(),
      open_count: 2,
      executed_count: 1,
      executed_orders: [
        {
          execId: "e1",
          symbol: "AAPL",
          contract: {
            conId: 1,
            symbol: "AAPL",
            secType: "STK",
            strike: null,
            right: null,
            expiry: null,
          },
          side: "BOT",
          quantity: 10,
          avgPrice: 100,
          commission: 1,
          realizedPNL: null,
          time: NOW.toISOString(),
          exchange: "NYSE",
        },
      ],
      open_orders: [
        makeOpenOrder({ filled: 0, remaining: 10, totalQuantity: 10, status: "Submitted" }),
        makeOpenOrder({
          orderId: 2,
          permId: 1002,
          filled: 4,
          remaining: 6,
          totalQuantity: 10,
          status: "Submitted",
          symbol: "MSFT",
          contract: {
            conId: 2,
            symbol: "MSFT",
            secType: "STK",
            strike: null,
            right: null,
            expiry: null,
          },
        }),
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

    const strip = screen.getByTestId("orders-command-strip");
    expect(strip.textContent).toMatch(/Working\s*1/);
    expect(strip.textContent).toMatch(/Partial\s*1/);
    expect(strip.textContent).toMatch(/Fills today\s*1/);
    expect(strip.textContent).toMatch(/Last sync\s*5m ago/);

    expect(strip.querySelector('a[href="#orders-open"]')).toBeTruthy();
    expect(strip.querySelector('a[href="#orders-executed"]')).toBeTruthy();
    expect(strip.querySelector('a[href="#orders-historical"]')).toBeTruthy();
    expect(strip.querySelector('a[href="#orders-cash"]')).toBeTruthy();

    expect(document.getElementById("orders-open")).toBeTruthy();
    expect(document.getElementById("orders-executed")).toBeTruthy();
    expect(document.getElementById("orders-historical")).toBeTruthy();
  });

  it("shows mapped Working status, fill qty, Δ Fill header, and intent OPEN", () => {
    const orders: OrdersData = {
      last_sync: NOW.toISOString(),
      open_count: 1,
      executed_count: 0,
      executed_orders: [],
      open_orders: [
        makeOpenOrder({
          action: "BUY",
          filled: 3,
          remaining: 7,
          totalQuantity: 10,
          limitPrice: 100,
          status: "Submitted",
        }),
      ],
    };

    render(
      React.createElement(WorkspaceSections, {
        section: "orders",
        orders,
        prices: {
          AAPL: {
            symbol: "AAPL",
            last: 100.3,
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
          },
        },
        portfolio: null,
      }),
    );

    // Status pill maps partial fills to Partial (command strip also has a Partial count).
    expect(screen.getAllByText("Partial").length).toBeGreaterThan(0);
    expect(screen.getByText("3/10")).toBeTruthy();
    expect(screen.getByText("Δ Fill")).toBeTruthy();
    expect(screen.getByText("+0.30")).toBeTruthy();
    expect(screen.getByText("OPEN")).toBeTruthy();

    const modify = screen.getByRole("button", { name: "MODIFY" });
    expect(modify.getAttribute("title")).toBe("Modify limit price");
  });

  it("opens cancel dialog for combo CANCEL ALL instead of cancelling immediately", () => {
    const buy = makeOpenOrder({
      orderId: 10,
      permId: 10,
      action: "BUY",
      symbol: "AMD",
      limitPrice: 2,
      totalQuantity: 1,
      orderRef: "combo-test",
      filled: 0,
      remaining: 1,
      contract: {
        conId: 10,
        symbol: "AMD",
        secType: "OPT",
        strike: 100,
        right: "C",
        expiry: "2026-08-21",
      },
    });
    const sell = makeOpenOrder({
      orderId: 11,
      permId: 11,
      action: "SELL",
      symbol: "AMD",
      limitPrice: 1,
      totalQuantity: 1,
      orderRef: "combo-test",
      filled: 0,
      remaining: 1,
      contract: {
        conId: 11,
        symbol: "AMD",
        secType: "OPT",
        strike: 110,
        right: "C",
        expiry: "2026-08-21",
      },
    });

    const orders: OrdersData = {
      last_sync: NOW.toISOString(),
      open_count: 2,
      executed_count: 0,
      executed_orders: [],
      open_orders: [buy, sell],
    };

    render(
      React.createElement(WorkspaceSections, {
        section: "orders",
        orders,
        prices: {},
        portfolio: null,
      }),
    );

    const cancelAll = screen.getByRole("button", { name: "CANCEL ALL" });
    fireEvent.click(cancelAll);

    expect(requestCancel).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "Cancel Orders" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel All" })).toBeTruthy();
  });
});

describe("open-order session window", () => {
  function renderMixedSessionOrders() {
    const optionDay = makeOpenOrder({
      orderId: 1,
      permId: 1001,
      tif: "DAY",
      symbol: "AAPL",
      contract: {
        conId: 10,
        symbol: "AAPL",
        secType: "OPT",
        strike: 200,
        right: "C",
        expiry: "2026-08-21",
      },
    });
    const tqqqExt = makeOpenOrder({
      orderId: 2,
      permId: 1002,
      tif: "GTC",
      outsideRth: true,
      symbol: "TQQQ",
      contract: {
        conId: 20,
        symbol: "TQQQ",
        secType: "STK",
        strike: null,
        right: null,
        expiry: null,
      },
    });
    const orders: OrdersData = {
      last_sync: NOW.toISOString(),
      open_count: 2,
      executed_count: 0,
      executed_orders: [],
      open_orders: [optionDay, tqqqExt],
    };
    render(
      React.createElement(WorkspaceSections, {
        section: "orders",
        orders,
        prices: {},
        portfolio: null,
      }),
    );
    return { optionDay, tqqqExt };
  }

  it("shows RTH chip on option DAY and EXT chip on TQQQ GTC outsideRth", () => {
    renderMixedSessionOrders();

    const optionRow = screen.getByTestId("open-order-row-1-1001");
    const tqqqRow = screen.getByTestId("open-order-row-2-1002");

    const optionChip = optionRow.querySelector('[data-testid="order-session-chip"]');
    const tqqqChip = tqqqRow.querySelector('[data-testid="order-session-chip"]');
    expect(optionChip).toBeTruthy();
    expect(tqqqChip).toBeTruthy();
    expect(optionChip?.getAttribute("data-session")).toBe("rth-only");
    expect(optionChip?.textContent).toBe("RTH");
    expect(optionChip?.getAttribute("title")).toMatch(/will not fill after 16:00 ET/i);
    expect(tqqqChip?.getAttribute("data-session")).toBe("extended");
    expect(tqqqChip?.textContent).toBe("EXT");
    expect(tqqqChip?.getAttribute("title")).toMatch(/can fill after 16:00 ET/i);
    expect(optionChip?.getAttribute("title") ?? "").not.toMatch(/\u2014/);
    expect(tqqqChip?.getAttribute("title") ?? "").not.toMatch(/\u2014/);

    expect(optionRow.textContent).toMatch(/DAY/);
    expect(tqqqRow.textContent).toMatch(/GTC/);
    expect(tqqqRow.className).toMatch(/open-order-row--ext/);
    expect(optionRow.className).not.toMatch(/open-order-row--ext/);
  });

  it("puts RTH/EXT counts next to ORDERS, not on the command strip", () => {
    renderMixedSessionOrders();

    const strip = screen.getByTestId("orders-command-strip");
    expect(strip.querySelector('[data-testid="open-orders-rth-count"]')).toBeNull();
    expect(strip.querySelector('[data-testid="open-orders-ext-count"]')).toBeNull();
    expect(strip.querySelectorAll(".orders-command-strip__stat")).toHaveLength(4);

    expect(screen.getByTestId("open-orders-rth-count").textContent).toBe("1 RTH");
    expect(screen.getByTestId("open-orders-ext-count").textContent).toBe("1 EXT");
    expect(screen.getByText("2 ORDERS")).toBeTruthy();
  });

  it("keeps session chips visible after hiding the TIF column", () => {
    renderMixedSessionOrders();

    fireEvent.click(screen.getByTitle("Show or hide columns"));
    fireEvent.click(screen.getByRole("checkbox", { name: "TIF" }));

    const optionRow = screen.getByTestId("open-order-row-1-1001");
    const tqqqRow = screen.getByTestId("open-order-row-2-1002");
    expect(optionRow.querySelector('[data-testid="order-session-chip"]')?.textContent).toBe("RTH");
    expect(tqqqRow.querySelector('[data-testid="order-session-chip"]')?.textContent).toBe("EXT");
  });
});
