/**
 * @vitest-environment jsdom
 *
 * §3.6 WorkspaceSections open-order dialogs. cancelTarget / cancelCombo /
 * modifyTarget / actionLoading were independent: Modal ×, Escape and backdrop
 * closed the dialog while the cancel was still in flight, a second click in
 * the same tick sent a second cancel, and a late `finally` could close a
 * dialog opened after it. The real Modal is rendered here (not mocked) so the
 * dismiss gate is exercised end to end.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import WorkspaceSections from "@/components/WorkspaceSections";
import Modal from "@/components/Modal";
import type { OpenOrder, OrdersData } from "@/lib/types";

vi.mock("@/components/TickerLink", () => ({
  default: (props: { ticker: string }) => React.createElement("span", null, props.ticker),
}));
vi.mock("@/components/ModifyOrderModal", () => ({ default: () => null }));
vi.mock("@/components/PerformancePanel", () => ({ default: () => null }));
vi.mock("@/components/RegimePanel", () => ({ default: () => null }));
vi.mock("@/components/CtaPage", () => ({ default: () => null }));
vi.mock("@/components/TickerWorkspace", () => ({ default: () => null }));
vi.mock("@/components/CashFlowsSection", () => ({ default: () => null }));

let resolveCancel: (() => void) | null = null;
const requestCancel = vi.fn(
  () => new Promise<void>((resolve) => {
    resolveCancel = resolve;
  }),
);

vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({
    pendingCancels: new Map(),
    pendingModifies: new Map(),
    cancelledOrders: [],
    requestCancel,
    requestModify: vi.fn(),
    pushNotification: vi.fn(),
  }),
  OrderActionsProvider: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("@/lib/useBlotter", () => ({
  useBlotter: () => ({ data: null, loading: false, error: null, syncing: false, syncNow: vi.fn() }),
}));
vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true, width: 1200 }),
}));
vi.mock("@/lib/useRiskFreeRate", () => ({ useRiskFreeRate: () => 0.04 }));

function order(over: Partial<OpenOrder>): OpenOrder {
  return {
    orderId: 1,
    permId: 101,
    symbol: "AAPL",
    contract: { conId: 1, symbol: over.symbol ?? "AAPL", secType: "STK", strike: null, right: null, expiry: null },
    action: "BUY",
    orderType: "LMT",
    totalQuantity: 10,
    limitPrice: 100,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: 10,
    avgFillPrice: null,
    tif: "DAY",
    ...over,
  };
}

function renderOrders(open: OpenOrder[]) {
  const orders: OrdersData = {
    last_sync: new Date().toISOString(),
    open_count: open.length,
    executed_count: 0,
    executed_orders: [],
    open_orders: open,
  };
  return render(<WorkspaceSections section="orders" orders={orders} prices={{}} portfolio={null} />);
}

beforeEach(() => {
  resolveCancel = null;
  requestCancel.mockClear();
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
});

const ORDERS = [
  order({ orderId: 1, permId: 101, symbol: "AAPL" }),
  order({ orderId: 2, permId: 202, symbol: "MSFT" }),
];

function startCancelOnFirstRow() {
  const row = screen.getByTestId("open-order-row-1-101");
  fireEvent.click(row.querySelector(".btn-cancel") as HTMLElement);
  fireEvent.click(screen.getByRole("button", { name: "Cancel Order" }));
}

describe("open-order cancel dialog while the request is in flight", () => {
  it("Escape, × and the backdrop do not close it; it closes when the request settles", async () => {
    renderOrders(ORDERS);
    startCancelOnFirstRow();
    expect(requestCancel).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Cancelling..." })).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });
    const close = screen.getByRole("button", { name: "Close" }) as HTMLButtonElement;
    expect(close.disabled).toBe(true);
    fireEvent.click(close);
    fireEvent.click(document.querySelector(".modal-backdrop") as HTMLElement);
    expect(screen.getByRole("dialog")).toBeTruthy();

    // No second dialog can open over the in-flight one.
    fireEvent.click(screen.getByTestId("open-order-row-2-202"));
    fireEvent.keyDown(document, { key: "x" });
    expect(screen.getAllByRole("dialog")).toHaveLength(1);

    await act(async () => {
      resolveCancel?.();
    });
    await vi.waitFor(() => expect(document.querySelector(".modal-backdrop--exiting, .modal-backdrop")).toBeNull());
  });

  it("a double click in one tick sends one cancel", async () => {
    renderOrders(ORDERS);
    fireEvent.click(screen.getByTestId("open-order-row-1-101").querySelector(".btn-cancel") as HTMLElement);
    const confirm = screen.getByRole("button", { name: "Cancel Order" });
    act(() => {
      confirm.click();
      confirm.click();
    });
    expect(requestCancel).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveCancel?.();
    });
  });

  it("sends the cancel for the live order the operator confirmed", () => {
    renderOrders(ORDERS);
    expect(requestCancel).not.toHaveBeenCalled();
    startCancelOnFirstRow();
    expect(requestCancel).toHaveBeenCalledWith(ORDERS[0]);
  });
});

describe("Modal dismissible", () => {
  it("routes ×, Escape and backdrop to onClose only while dismissible", () => {
    const onClose = vi.fn();
    const view = render(<Modal open onClose={onClose} title="T" dismissible={false}>body</Modal>);
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).not.toHaveBeenCalled();
    view.rerender(<Modal open onClose={onClose} title="T">body</Modal>);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
