/**
 * @vitest-environment jsdom
 *
 * §3.6 OrderTab:
 *   - CANCEL on an existing order fired the broker cancel in one click, with
 *     no confirm (every other surface routes through CancelOrderDialog) and a
 *     local loading flag duplicating the context's pendingCancels.
 *   - R-281: the new-order ticket had no synchronous in-flight guard, so two
 *     clicks in one tick placed two orders.
 *   - Changing TIF left the ticket armed on the reviewed (other) TIF.
 * Rendered with the REAL OrderActionsProvider (the cancel fetch owner) and a
 * stubbed fetch; assertions are on the wire.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { OpenOrder, PortfolioData } from "@/lib/types";
import OrderTab from "@/components/ticker-detail/OrderTab";
import { OrderActionsProvider } from "@/lib/OrderActionsContext";

vi.mock("@/lib/useRiskFreeRate", () => ({ useRiskFreeRate: () => 0 }));
vi.mock("@/components/research/ResearchTicketContext", () => ({ default: () => null }));
vi.mock("@/components/Modal", () => ({
  default: ({ open, title, children }: { open: boolean; title: string; children: React.ReactNode }) =>
    open ? React.createElement("div", { role: "dialog", "aria-label": title }, children) : null,
}));

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

const mutations = (calls: RecordedCall[]) => calls.filter((c) => c.method !== "GET");

const ORDER: OpenOrder = {
  orderId: 11,
  permId: 1101,
  symbol: "AAPL",
  contract: { conId: 265598, symbol: "AAPL", secType: "STK", strike: null, right: null, expiry: null },
  action: "SELL",
  orderType: "LMT",
  totalQuantity: 100,
  limitPrice: 171,
  auxPrice: null,
  status: "Submitted",
  filled: 0,
  remaining: 100,
  avgFillPrice: null,
  tif: "DAY",
};

const PORTFOLIO = { positions: [], bankroll: 1_000_000 } as unknown as PortfolioData;

function renderTab(openOrders: OpenOrder[] = []) {
  return render(
    <OrderActionsProvider>
      <OrderTab ticker="AAPL" position={null} portfolio={PORTFOLIO} prices={{}} openOrders={openOrders} />
    </OrderActionsProvider>,
  );
}

describe("OrderTab existing-order cancel goes through CancelOrderDialog", () => {
  it("CANCEL opens the confirm and sends nothing; confirming sends the exact request", async () => {
    const calls = recordFetch();
    renderTab([ORDER]);

    fireEvent.click(screen.getByRole("button", { name: "CANCEL" }));
    await act(async () => {});
    expect(screen.getByRole("dialog", { name: "Cancel Order" })).toBeTruthy();
    expect(mutations(calls)).toEqual([]);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancel Order" }));
    });
    const cancels = mutations(calls).filter((c) => c.url === "/api/orders/cancel");
    expect(cancels).toHaveLength(1);
    expect(cancels[0].method).toBe("POST");
    expect(cancels[0].body).toEqual({ orderId: 11, permId: 1101 });
  });

  it("Keep Order closes the confirm without a request", async () => {
    const calls = recordFetch();
    renderTab([ORDER]);
    fireEvent.click(screen.getByRole("button", { name: "CANCEL" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep Order" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await act(async () => {});
    expect(mutations(calls)).toEqual([]);
  });
});

function armNewOrderTicket() {
  fireEvent.change(screen.getByPlaceholderText("Shares"), { target: { value: "10" } });
  const limit = document.querySelectorAll<HTMLInputElement>("input.modify-price-input");
  fireEvent.change(limit[limit.length - 1], { target: { value: "170.00" } });
  fireEvent.click(screen.getByRole("button", { name: "Place Order" }));
  return screen.getByRole("button", { name: "Confirm Order" });
}

describe("OrderTab new-order ticket guards", () => {
  it("R-281: two Confirm clicks in one tick place ONE order", async () => {
    const calls = recordFetch();
    renderTab();
    const confirm = armNewOrderTicket();
    expect(mutations(calls)).toEqual([]);

    await act(async () => {
      confirm.click();
      confirm.click();
    });
    const places = mutations(calls).filter((c) => c.url === "/api/orders/place");
    expect(places).toHaveLength(1);
    expect(places[0].method).toBe("POST");
    expect(places[0].body).toMatchObject({ type: "stock", symbol: "AAPL", quantity: 10, limitPrice: 170, tif: "DAY" });
  });

  it("changing TIF disarms the confirm step", () => {
    const calls = recordFetch();
    renderTab();
    armNewOrderTicket();
    fireEvent.click(screen.getByRole("button", { name: "GTC" }));
    expect(screen.queryByRole("button", { name: "Confirm Order" })).toBeNull();
    expect(screen.getByRole("button", { name: "Place Order" })).toBeTruthy();
    expect(mutations(calls)).toEqual([]);
  });
});
