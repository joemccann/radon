/**
 * @vitest-environment jsdom
 *
 * REL-236 / R-641: the modify modal painted last-tick marks from a half-open
 * relay socket and nothing disarmed the Modify button. The gate is asserted
 * at the wire through the REAL OrderActionsProvider (which OWNS the
 * /api/orders/modify fetch): the armed path proves the full URL, method and
 * exact payload; the closed paths prove NO order request fires at all.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { OpenOrder, PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import type { ModifyOrderRequest } from "@/lib/orderModify";
import ModifyOrderModal from "@/components/ModifyOrderModal";
import { OrderActionsProvider, useOrderActions } from "@/lib/OrderActionsContext";
import { QUOTE_SUBMIT_MAX_AGE_MS } from "@/lib/order/quoteSubmitGate";

vi.mock("@/lib/useRiskFreeRate", () => ({
  useRiskFreeRate: () => 0,
}));

vi.mock("@/components/Modal", () => ({
  default: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? React.createElement("div", { className: "mock-modal" }, children) : null,
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// T-480: drain any queued follow-up on FAKE timers so the "nothing fired"
// window cannot lose a wall-clock race to React's commit/effect flush.
async function flushTimersFake() {
  vi.useFakeTimers();
  try {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
  } finally {
    vi.useRealTimers();
  }
}

type RecordedCall = { url: string; method: string; body: unknown };

function recordFetch(): RecordedCall[] {
  const calls: RecordedCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
      calls.push({ url, method: init?.method ?? "GET", body });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

function orderCalls(calls: RecordedCall[]): RecordedCall[] {
  return calls.filter((call) => call.url.startsWith("/api/orders"));
}

function tqqqOrder(): OpenOrder {
  return {
    orderId: 2,
    permId: 1002,
    symbol: "TQQQ",
    contract: {
      conId: 20,
      symbol: "TQQQ",
      secType: "STK",
      strike: null,
      right: null,
      expiry: null,
    },
    action: "BUY",
    orderType: "LMT",
    totalQuantity: 10,
    limitPrice: 50,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: 10,
    avgFillPrice: null,
    tif: "GTC",
  };
}

function tqqqQuote(timestamp: string): PriceData {
  return {
    symbol: "TQQQ",
    last: 51,
    lastIsCalculated: false,
    bid: 50.9,
    ask: 51.1,
    bidSize: 1,
    askSize: 1,
    volume: 1000,
    high: null,
    low: null,
    open: null,
    close: 50.5,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp,
  };
}

const PORTFOLIO = { positions: [], bankroll: 1_000_000 } as unknown as PortfolioData;

/** Renders the modal wired to the REAL provider's requestModify. */
function Harness({
  order,
  prices,
  feedConnected,
}: {
  order: OpenOrder;
  prices: Record<string, PriceData>;
  feedConnected?: boolean;
}) {
  const { requestModify } = useOrderActions();
  const onConfirm = (request: ModifyOrderRequest) => {
    void requestModify(order, request);
  };
  return (
    <ModifyOrderModal
      order={order}
      loading={false}
      prices={prices}
      portfolio={PORTFOLIO}
      onConfirm={onConfirm}
      onClose={() => {}}
      feedConnected={feedConnected}
    />
  );
}

function renderModal(prices: Record<string, PriceData>, feedConnected?: boolean) {
  return render(
    <OrderActionsProvider>
      <Harness order={tqqqOrder()} prices={prices} feedConnected={feedConnected} />
    </OrderActionsProvider>,
  );
}

const priceInput = () => document.getElementById("modify-price-input") as HTMLInputElement;
const submitBtn = () =>
  screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement;

describe("ModifyOrderModal quote gate at the wire (R-641)", () => {
  it("ARMED: fresh quote sends the exact modify request", async () => {
    const calls = recordFetch();
    renderModal({ TQQQ: tqqqQuote(new Date().toISOString()) });

    fireEvent.change(priceInput(), { target: { value: "51.25" } });
    await waitFor(() => expect(submitBtn().disabled).toBe(false));
    fireEvent.click(submitBtn());

    await waitFor(() => expect(orderCalls(calls)).toHaveLength(1));
    const call = orderCalls(calls)[0];
    expect(call.url).toBe("/api/orders/modify");
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({ orderId: 2, permId: 1002, newPrice: 51.25 });
  });

  it("CLOSED: a stale quote disarms Modify and NO order request fires", async () => {
    const calls = recordFetch();
    renderModal({
      TQQQ: tqqqQuote(new Date(Date.now() - QUOTE_SUBMIT_MAX_AGE_MS - 5 * 60_000).toISOString()),
    });

    fireEvent.change(priceInput(), { target: { value: "51.25" } });
    expect(submitBtn().disabled).toBe(true);
    fireEvent.click(submitBtn());
    await flushTimersFake();
    expect(orderCalls(calls)).toHaveLength(0);
  });

  it("CLOSED: a disconnected feed disarms Modify and NO order request fires", async () => {
    const calls = recordFetch();
    renderModal({ TQQQ: tqqqQuote(new Date().toISOString()) }, false);

    fireEvent.change(priceInput(), { target: { value: "51.25" } });
    expect(submitBtn().disabled).toBe(true);
    fireEvent.click(submitBtn());
    await flushTimersFake();
    expect(orderCalls(calls)).toHaveLength(0);
  });
});
