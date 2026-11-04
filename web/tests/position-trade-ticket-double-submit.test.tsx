/** @vitest-environment jsdom */
//
// R-281 on the position trade ticket. `loading` is React state, so a second
// Confirm in the same tick still sees `false`; only a ref stops a double-send.
// The other tickets carry that ref; this one did not.

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("@/lib/RealtimePricesContext", () => ({
  useRealtimePrices: () => ({ connected: true, ibConnected: true, marketDataDegraded: false }),
}));
vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActionsOptional: () => ({ pushNotification: vi.fn() }),
}));
vi.mock("@/components/ComboSkewPanel", () => ({ default: () => null }));

import PositionTradeTicket from "../components/ticker-detail/PositionTradeTicket";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function makePrice(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol, last: (bid + ask) / 2, lastIsCalculated: false, bid, ask, bidSize: 1, askSize: 1,
    volume: 100, high: null, low: null, open: null, close: (bid + ask) / 2, week52High: null,
    week52Low: null, avgVolume: null, delta: null, gamma: null, theta: null, vega: null,
    impliedVol: null, undPrice: null, timestamp: new Date().toISOString(),
  };
}

const POSITION = {
  id: 7,
  ticker: "MU",
  structure: "Risk Reversal (P$800.0/C$1050.0)",
  structure_type: "Risk Reversal",
  direction: "COMBO",
  contracts: 5,
  expiry: "2026-07-17",
  entry_date: "2026-05-29",
  entry_cost: -3495,
  market_value: -46290,
  legs: [
    { direction: "SHORT", type: "Call", strike: 1050, contracts: 3, avg_cost: 10999, entry_cost: -32997, market_price: 133.93, market_price_is_calculated: false },
    { direction: "LONG", type: "Put", strike: 800, contracts: 5, avg_cost: 5900, entry_cost: 29500, market_price: 41.0, market_price_is_calculated: false },
  ],
} as unknown as PortfolioPosition;

const PRICES: Record<string, PriceData> = {
  MU_20260717_1050_C: makePrice("MU_20260717_1050_C", 130, 134),
  MU_20260717_800_P: makePrice("MU_20260717_800_P", 40, 42),
};

const PORTFOLIO = {
  bankroll: 250_000,
  positions: [POSITION],
  account_summary: { net_liquidation: 250_000, excess_liquidity: 240_250, buying_power: 480_500 },
} as unknown as PortfolioData;

/** The React click handler behind a node, so it can be called twice in one tick. */
function reactOnClick(node: HTMLElement): () => Promise<void> {
  const key = Object.keys(node).find((k) => k.startsWith("__reactProps$"))!;
  return (node as unknown as Record<string, { onClick: () => Promise<void> }>)[key].onClick;
}

describe("PositionTradeTicket in-flight guard", () => {
  it("two Confirm calls in one tick send exactly one order", async () => {
    type Sent = { url: string; method: string; body: unknown };
    const sent: Sent[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        if (method === "POST") sent.push({ url: String(input), method, body: JSON.parse(String(init?.body)) });
        return new Response(JSON.stringify({ status: "Submitted", orderId: 1 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(
      <PositionTradeTicket
        position={POSITION}
        prices={PRICES}
        portfolio={PORTFOLIO}
        target={{ kind: "leg", index: 1 }}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByTestId("position-trade-limit"), { target: { value: "41.00" } });
    fireEvent.click(await screen.findByRole("button", { name: /review order/i }));
    expect(sent).toHaveLength(0);

    const confirm = await screen.findByRole("button", { name: /confirm order/i });
    await waitFor(() => expect((confirm as HTMLButtonElement).disabled).toBe(false));

    await act(async () => {
      const click = reactOnClick(confirm);
      void click();
      void click();
    });

    await waitFor(() => expect(sent.length).toBeGreaterThan(0));
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe("/api/orders/place");
    expect(sent[0].method).toBe("POST");
    expect(sent[0].body).toMatchObject({ action: "SELL", quantity: 5, limitPrice: 41 });
  });
});
