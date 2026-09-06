/**
 * @vitest-environment jsdom
 *
 * T-462: the REL-236 disconnect arm of quoteSubmitGate is dead in
 * production. `feedConnected` is optional on ModifyOrderModal and
 * SingleLegOrderTicket, and no production call site supplied it, so a
 * disconnected feed left the gate OPEN (`undefined !== false`). The leaf
 * specs pass `feedConnected` from the harness and prove nothing about the
 * wiring. Per CLAUDE.md, a gated action is tested at the wire: this file
 * renders OrderTab — the owner surface whose tree owns the modify fetch
 * via OrderActionsContext — with the realtime feed disconnected, drives
 * Modify in its otherwise-armed state, and asserts ZERO requests fire and
 * the blocked reason renders. The armed case pins the exact request so a
 * wrong URL, method, or payload also reds.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { OpenOrder, PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import OrderTab from "@/components/ticker-detail/OrderTab";
import { OrderActionsProvider } from "@/lib/OrderActionsContext";

vi.mock("@/lib/useRiskFreeRate", () => ({
  useRiskFreeRate: () => 0,
}));

vi.mock("@/components/Modal", () => ({
  default: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? React.createElement("div", { className: "mock-modal" }, children) : null,
}));

/** Feed connectivity the four production surfaces read. Mutable so the
 *  disconnected and connected cases exercise the SAME call-site wiring. */
let feedConnectedState = false;
vi.mock("@/lib/RealtimePricesContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/RealtimePricesContext")>();
  return {
    ...actual,
    useRealtimePrices: () => ({
      ...actual.useRealtimePrices(),
      connected: feedConnectedState,
    }),
  };
});

beforeEach(() => {
  feedConnectedState = false;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

function aaplStockOrder(): OpenOrder {
  return {
    orderId: 11,
    permId: 1101,
    symbol: "AAPL",
    contract: { conId: 265598, symbol: "AAPL", secType: "STK" },
    action: "SELL",
    orderType: "LMT",
    totalQuantity: 100,
    limitPrice: 171,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: 100,
    avgFillPrice: 0,
    tif: "DAY",
  } as OpenOrder;
}

function freshAaplQuote(): PriceData {
  return {
    symbol: "AAPL",
    last: 171,
    lastIsCalculated: false,
    bid: 170,
    ask: 172,
    bidSize: 1,
    askSize: 1,
    volume: 1000,
    high: null,
    low: null,
    open: null,
    close: 171,
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

const PORTFOLIO = { positions: [], bankroll: 1_000_000 } as unknown as PortfolioData;

type RecordedCall = { url: string; method: string; body: unknown };

function recordFetch(): RecordedCall[] {
  const calls: RecordedCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
      calls.push({ url, method: init?.method ?? "GET", body });
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    }),
  );
  return calls;
}

/** OrderTab's stock form issues a read (GET /api/short-availability/:sym)
 *  on mount; the gate governs order mutations, so assert on those. */
function mutationCalls(calls: RecordedCall[]): RecordedCall[] {
  return calls.filter((c) => c.method !== "GET");
}

function renderOrderTab() {
  return render(
    <OrderActionsProvider>
      <OrderTab
        ticker="AAPL"
        position={null}
        portfolio={PORTFOLIO}
        prices={{ AAPL: freshAaplQuote() }}
        openOrders={[aaplStockOrder()]}
      />
    </OrderActionsProvider>,
  );
}

/** Open the modify modal from the open-orders row and arm it by editing
 *  the limit price — everything short of feed connectivity is satisfied. */
function openAndArmModify() {
  fireEvent.click(screen.getByRole("button", { name: /^MODIFY$/ }));
  const priceInput = document.getElementById("modify-price-input") as HTMLInputElement;
  fireEvent.change(priceInput, { target: { value: "170.50" } });
  return screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement;
}

describe("OrderTab modify gate wired to the live feed (T-462)", () => {
  it("DISCONNECTED: submit is disarmed, the blocked reason renders, and NO request fires", async () => {
    feedConnectedState = false;
    const calls = recordFetch();
    renderOrderTab();

    const submit = openAndArmModify();

    expect(
      screen.getByText("Live feed disconnected. Submit disabled until quotes resume."),
    ).toBeTruthy();
    expect(submit.disabled).toBe(true);
    fireEvent.click(submit);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(mutationCalls(calls)).toEqual([]);
    expect(calls.some((c) => c.url === "/api/orders/modify")).toBe(false);
  });

  it("CONNECTED: the same armed state submits the exact modify request", async () => {
    feedConnectedState = true;
    const calls = recordFetch();
    renderOrderTab();

    const submit = openAndArmModify();

    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);

    await waitFor(() => expect(mutationCalls(calls)).toHaveLength(1));
    const wire = mutationCalls(calls)[0];
    expect(wire.url).toBe("/api/orders/modify");
    expect(wire.method).toBe("POST");
    expect(wire.body).toEqual({ orderId: 11, permId: 1101, newPrice: 170.5 });
  });
});
