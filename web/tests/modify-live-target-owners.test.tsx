/**
 * @vitest-environment jsdom
 *
 * §1 #7 / R-633 at the OWNER: ModifyOrderModal's fill-race reprompt compares
 * its seed snapshot to the live `order` prop, but both owners (the /orders
 * WorkspaceSections table and the ticker-detail OrderTab) passed the frozen
 * OpenOrder captured at click time, so `fillsAdvanced` was always false and a
 * quantity edit transmitted stale-fill math. These tests render the owners
 * with the REAL OrderActionsProvider (the fetch owner) and a stubbed fetch,
 * advance fills on the polled orders, and assert at the wire.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { OpenOrder, OrdersData, PortfolioData } from "@/lib/types";
import WorkspaceSections from "@/components/WorkspaceSections";
import OrderTab from "@/components/ticker-detail/OrderTab";
import ModifyOrderModal from "@/components/ModifyOrderModal";
import { OrderActionsProvider, useOrderActions, type Notification } from "@/lib/OrderActionsContext";

vi.mock("@/components/TickerLink", () => ({
  default: (props: { ticker: string }) => React.createElement("span", null, props.ticker),
}));
vi.mock("@/components/Modal", () => ({
  default: ({ open, title, children }: { open: boolean; title: string; children: React.ReactNode }) =>
    open ? React.createElement("div", { role: "dialog", "aria-label": title }, children) : null,
}));
vi.mock("@/components/PerformancePanel", () => ({ default: () => null }));
vi.mock("@/components/RegimePanel", () => ({ default: () => null }));
vi.mock("@/components/CtaPage", () => ({ default: () => null }));
vi.mock("@/components/TickerWorkspace", () => ({ default: () => null }));
vi.mock("@/components/CashFlowsSection", () => ({ default: () => null }));
vi.mock("@/components/research/ResearchTicketContext", () => ({ default: () => null }));
vi.mock("@/lib/useBlotter", () => ({
  useBlotter: () => ({ data: null, loading: false, error: null, syncing: false, syncNow: vi.fn() }),
}));
vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true, width: 1200 }),
}));
vi.mock("@/lib/useRiskFreeRate", () => ({
  useRiskFreeRate: () => 0,
  useRiskFreeRateState: () => ({ rate: 0 }),
}));
vi.mock("@/lib/RealtimePricesContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/RealtimePricesContext")>();
  return {
    ...actual,
    useRealtimePrices: () => ({
      ...actual.useRealtimePrices(),
      connected: true,
      ibConnected: true,
      marketDataDegraded: false,
    }),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

type RecordedCall = { url: string; method: string; body: unknown };

function recordFetch(): RecordedCall[] {
  const calls: RecordedCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
      calls.push({ url, method: init?.method ?? "GET", body });
      return new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

const modifyCalls = (calls: RecordedCall[]) => calls.filter((c) => c.url === "/api/orders/modify");

function aapl(over: Partial<OpenOrder> = {}): OpenOrder {
  return {
    orderId: 11,
    permId: 1101,
    symbol: "AAPL",
    contract: { conId: 265598, symbol: "AAPL", secType: "STK", strike: null, right: null, expiry: null },
    action: "BUY",
    orderType: "LMT",
    totalQuantity: 100,
    limitPrice: 171,
    auxPrice: null,
    status: "Submitted",
    filled: 10,
    remaining: 90,
    avgFillPrice: 171,
    tif: "DAY",
    ...over,
  };
}

const PORTFOLIO = { positions: [], bankroll: 1_000_000 } as unknown as PortfolioData;

let drained: Notification[] = [];
function NotificationProbe() {
  const { drainNotifications } = useOrderActions();
  drained.push(...drainNotifications());
  return null;
}

function ordersData(open: OpenOrder[]): OrdersData {
  return {
    last_sync: new Date().toISOString(),
    open_count: open.length,
    executed_count: 0,
    executed_orders: [],
    open_orders: open,
  };
}

function renderWorkspace(open: OpenOrder[]) {
  const tree = (o: OpenOrder[]) => (
    <OrderActionsProvider>
      <NotificationProbe />
      <WorkspaceSections section="orders" orders={ordersData(o)} prices={{}} portfolio={PORTFOLIO} />
    </OrderActionsProvider>
  );
  const view = render(tree(open));
  return (next: OpenOrder[]) => view.rerender(tree(next));
}

function renderOrderTab(open: OpenOrder[]) {
  const tree = (o: OpenOrder[]) => (
    <OrderActionsProvider>
      <NotificationProbe />
      <OrderTab ticker="AAPL" position={null} portfolio={PORTFOLIO} prices={{}} openOrders={o} />
    </OrderActionsProvider>
  );
  const view = render(tree(open));
  return (next: OpenOrder[]) => view.rerender(tree(next));
}

const qtyInput = () => document.getElementById("modify-quantity-input") as HTMLInputElement;
const submitBtn = () => screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement;

const OWNERS = [
  ["WorkspaceSections", renderWorkspace],
  ["OrderTab", renderOrderTab],
] as const;

describe.each(OWNERS)("%s passes the LIVE order to ModifyOrderModal", (_name, renderOwner) => {
  it("a fill landing mid-edit reprompts instead of transmitting stale quantity math", async () => {
    drained = [];
    const calls = recordFetch();
    const rerenderWith = renderOwner([aapl()]);

    fireEvent.click(screen.getByRole("button", { name: /^MODIFY$/ }));
    expect(qtyInput().value).toBe("90");
    fireEvent.change(qtyInput(), { target: { value: "50" } });

    // 20 more fill while the dialog is open: 10 -> 30 filled.
    rerenderWith([aapl({ filled: 30, remaining: 70 })]);
    expect(screen.getByText(/fills advanced: 30 of 100 now filled/)).toBeTruthy();

    await act(async () => {
      fireEvent.click(submitBtn());
    });

    // Frozen snapshot sent newQuantity = 10 + 50 = 60 here.
    expect(modifyCalls(calls)).toEqual([]);
    expect(screen.getByText(/Fills advanced while editing: 70 now remaining/)).toBeTruthy();
    expect(qtyInput().value).toBe("70");
  });

  it("closes the modal with a notice when the order stops working", async () => {
    drained = [];
    recordFetch();
    const rerenderWith = renderOwner([aapl()]);
    fireEvent.click(screen.getByRole("button", { name: /^MODIFY$/ }));
    expect(screen.getByTestId("modify-dialog")).toBeTruthy();

    rerenderWith([aapl({ status: "Filled", filled: 100, remaining: 0 })]);
    expect(screen.queryByTestId("modify-dialog")).toBeNull();
    rerenderWith([]);
    expect(drained.some((n) => /no longer working/i.test(n.message))).toBe(true);
  });

  it("an armed price edit on the live order transmits the exact request", async () => {
    const calls = recordFetch();
    const rerenderWith = renderOwner([aapl()]);
    fireEvent.click(screen.getByRole("button", { name: /^MODIFY$/ }));
    rerenderWith([aapl({ filled: 30, remaining: 70 })]);
    const price = document.getElementById("modify-price-input") as HTMLInputElement;
    fireEvent.change(price, { target: { value: "170.50" } });
    await act(async () => {
      fireEvent.click(submitBtn());
    });
    expect(modifyCalls(calls)).toHaveLength(1);
    expect(modifyCalls(calls)[0].method).toBe("POST");
    expect(modifyCalls(calls)[0].body).toEqual({ orderId: 11, permId: 1101, newPrice: 170.5 });
  });
});

describe("ModifyOrderModal reseeds per order identity", () => {
  const modal = (order: OpenOrder) => (
    <OrderActionsProvider>
      <ModifyOrderModal order={order} loading={false} portfolio={PORTFOLIO} onConfirm={() => {}} onClose={() => {}} />
    </OrderActionsProvider>
  );

  it("two unacknowledged orders (permId 0) do not share a seed", () => {
    recordFetch();
    const view = render(modal(aapl({ permId: 0, orderId: 21, filled: 0, remaining: 100 })));
    expect(qtyInput().value).toBe("100");
    view.rerender(
      modal(aapl({ permId: 0, orderId: 22, totalQuantity: 40, filled: 0, remaining: 40, limitPrice: 12 })),
    );
    // Keyed on permId alone, the field kept the first order's 100 and 171.00.
    expect(qtyInput().value).toBe("40");
    expect((document.getElementById("modify-price-input") as HTMLInputElement).value).toBe("12.00");
  });
});
