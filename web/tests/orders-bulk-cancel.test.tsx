/**
 * @vitest-environment jsdom
 *
 * Bulk selection + Cancel selected wiring on desktop open orders.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import WorkspaceSections from "../components/WorkspaceSections";
import type { OpenOrder, OrdersData } from "../lib/types";

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
  default: () => React.createElement("div", { id: "orders-cash" }),
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
  const symbol = overrides.symbol ?? "AAPL";
  return {
    orderId: overrides.orderId ?? 1,
    permId: overrides.permId ?? 1001,
    symbol,
    contract: overrides.contract ?? {
      conId: overrides.orderId ?? 1,
      symbol,
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

function renderOrders(openOrders: OpenOrder[]) {
  const orders: OrdersData = {
    last_sync: NOW.toISOString(),
    open_count: openOrders.length,
    executed_count: 0,
    executed_orders: [],
    open_orders: openOrders,
  };
  return render(
    React.createElement(WorkspaceSections, {
      section: "orders",
      orders,
      prices: {},
      portfolio: null,
    }),
  );
}

beforeEach(() => {
  requestCancel.mockClear();
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("bulk cancel selection", () => {
  it("shows Cancel selected (N) after checking rows and opens multi cancel dialog", () => {
    renderOrders([
      makeOpenOrder({ orderId: 1, permId: 101, symbol: "AAPL" }),
      makeOpenOrder({ orderId: 2, permId: 102, symbol: "MSFT" }),
    ]);

    expect(screen.queryByTestId("cancel-selected-orders")).toBeNull();

    fireEvent.click(screen.getByTestId("open-order-select-1-101"));
    fireEvent.click(screen.getByTestId("open-order-select-2-102"));

    const bulkBtn = screen.getByTestId("cancel-selected-orders");
    expect(bulkBtn.textContent).toContain("Cancel selected (2)");

    fireEvent.click(bulkBtn);

    const dialog = screen.getByTestId("cancel-dialog-modal");
    expect(dialog).toBeTruthy();
    expect(within(dialog).getByText("Cancel Orders")).toBeTruthy();
    expect(within(dialog).getByText("2 symbols")).toBeTruthy();
    expect(within(dialog).getByText("2")).toBeTruthy();
  });

  it("select-all checks every visible row", () => {
    renderOrders([
      makeOpenOrder({ orderId: 1, permId: 201, symbol: "AAPL" }),
      makeOpenOrder({ orderId: 2, permId: 202, symbol: "MSFT" }),
    ]);

    fireEvent.click(screen.getByTestId("open-orders-select-all"));
    expect(screen.getByTestId("cancel-selected-orders").textContent).toContain(
      "Cancel selected (2)",
    );
  });

  it("clears selection after confirm cancel", async () => {
    renderOrders([
      makeOpenOrder({ orderId: 1, permId: 301, symbol: "AAPL" }),
      makeOpenOrder({ orderId: 2, permId: 302, symbol: "NVDA" }),
    ]);

    fireEvent.click(screen.getByTestId("open-order-select-1-301"));
    fireEvent.click(screen.getByTestId("cancel-selected-orders"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel All" }));

    await vi.waitFor(() => {
      expect(requestCancel).toHaveBeenCalledTimes(1);
    });
    await vi.waitFor(() => {
      expect(screen.queryByTestId("cancel-selected-orders")).toBeNull();
    });
  });
});

describe("keyboard shortcuts", () => {
  it("focuses open orders filter on /", () => {
    renderOrders([makeOpenOrder({ orderId: 1, permId: 401, symbol: "AAPL" })]);
    const filter = screen.getByTestId("orders-open-filter") as HTMLInputElement;
    expect(document.activeElement).not.toBe(filter);

    fireEvent.keyDown(document, { key: "/" });
    expect(document.activeElement).toBe(filter);
  });

  it("does not fire / while typing in the filter", () => {
    renderOrders([makeOpenOrder({ orderId: 1, permId: 402, symbol: "AAPL" })]);
    const filter = screen.getByTestId("orders-open-filter") as HTMLInputElement;
    filter.focus();
    fireEvent.keyDown(filter, { key: "/" });
    // Still focused; no prevent side-effects required beyond not stealing
    expect(document.activeElement).toBe(filter);
  });

  it("X opens cancel for the selected row", () => {
    renderOrders([makeOpenOrder({ orderId: 5, permId: 505, symbol: "AAPL" })]);
    fireEvent.click(screen.getByTestId("open-order-row-5-505"));
    fireEvent.keyDown(document, { key: "x" });

    const dialog = screen.getByTestId("cancel-dialog-modal");
    expect(within(dialog).getByRole("heading", { name: "Cancel Order" })).toBeTruthy();
    expect(within(dialog).getByText("AAPL")).toBeTruthy();
  });

  it("M opens modify path when row is LMT (modify modal mocked present via request path)", () => {
    // ModifyOrderModal is mocked null, but selecting + M should not throw.
    // Cancel dialog must remain closed.
    renderOrders([makeOpenOrder({ orderId: 6, permId: 606, symbol: "AAPL", orderType: "LMT" })]);
    fireEvent.click(screen.getByTestId("open-order-row-6-606"));
    fireEvent.keyDown(document, { key: "m" });
    expect(screen.queryByTestId("cancel-dialog-modal")).toBeNull();
  });

  it("marks selected row with selected class", () => {
    renderOrders([makeOpenOrder({ orderId: 7, permId: 707, symbol: "AAPL" })]);
    const row = screen.getByTestId("open-order-row-7-707");
    fireEvent.click(row);
    expect(row.className).toContain("open-order-row--selected");
  });
});
