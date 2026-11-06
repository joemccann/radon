/**
 * @vitest-environment jsdom
 *
 * OrderTab's ComboOrderForm and PositionTradeTicket build the transmitted
 * BAG legs and ratios from the LIVE `position` prop. With a boolean confirm
 * step, a portfolio poll that changed the held legs while the ticket was
 * armed changed the request without disarming it: the operator reviewed
 * 1:1 ratios and Transmit sent 2:1. Both tickets now arm for the exact
 * request body, so a changed position disarms, nothing is sent, and the
 * re-reviewed request is what reaches the wire.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("@/lib/RealtimePricesContext", () => ({
  useRealtimePrices: () => ({ connected: true, ibConnected: true, marketDataDegraded: false }),
}));
vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({
    pendingCancels: new Map(),
    pendingModifies: new Map(),
    cancelledOrders: [],
    requestCancel: vi.fn(),
    requestModify: vi.fn(),
    pushNotification: vi.fn(),
    drainNotifications: vi.fn(() => []),
    setOrdersUpdater: vi.fn(),
  }),
  useOrderActionsOptional: () => ({ pushNotification: vi.fn() }),
}));
vi.mock("@/components/ModifyOrderModal", () => ({ default: () => null }));
vi.mock("@/components/ComboSkewPanel", () => ({ default: () => null }));
vi.mock("@/lib/useRiskFreeRate", () => ({ useRiskFreeRate: () => 0 }));

import OrderTab from "../components/ticker-detail/OrderTab";
import PositionTradeTicket from "../components/ticker-detail/PositionTradeTicket";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type Call = { url: string; method: string; body: unknown };

function recordFetch(): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push({ url, method: init?.method ?? "GET", body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    return new Response(JSON.stringify({ status: "ok", orderId: 1 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }));
  return calls;
}

const places = (calls: Call[]) => calls.filter((c) => c.url === "/api/orders/place");

async function clickWhenEnabled(name: RegExp | string) {
  const button = await screen.findByRole("button", { name });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  await act(async () => { fireEvent.click(button); });
}

function makePrice(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol, last: (bid + ask) / 2, lastIsCalculated: false, bid, ask, bidSize: 1, askSize: 1, volume: 100,
    high: null, low: null, open: null, close: (bid + ask) / 2, week52High: null, week52Low: null, avgVolume: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
  };
}

function portfolioWith(position: PortfolioPosition): PortfolioData {
  return {
    bankroll: 250_000, peak_value: 250_000, last_sync: new Date().toISOString(),
    total_deployed_pct: 4, total_deployed_dollars: 9_750, remaining_capacity_pct: 96,
    position_count: 1, defined_risk_count: 0, undefined_risk_count: 1, avg_kelly_optimal: null,
    positions: [position],
    account_summary: {
      net_liquidation: 250_000, daily_pnl: 0, unrealized_pnl: 0, realized_pnl: 0, settled_cash: 240_250,
      maintenance_margin: 0, excess_liquidity: 240_250, buying_power: 480_500, dividends: 0,
    },
  } as unknown as PortfolioData;
}

function riskReversal(longCalls: number, shortPuts: number): PortfolioPosition {
  return {
    id: 12, ticker: "IWM", structure: "Risk Reversal (P$243.0/C$247.0)", structure_type: "Risk Reversal",
    risk_profile: "undefined", expiry: "2026-03-26", contracts: longCalls, direction: "COMBO",
    entry_cost: -579.79, max_risk: null, market_value: 750, kelly_optimal: null, target: null, stop: null,
    entry_date: "2026-03-19",
    legs: [
      { direction: "LONG", contracts: longCalls, type: "Call", strike: 247, entry_cost: 346 * longCalls, avg_cost: 346, market_price: 3.63, market_value: 363 * longCalls, market_price_is_calculated: false },
      { direction: "SHORT", contracts: shortPuts, type: "Put", strike: 243, entry_cost: 357 * shortPuts, avg_cost: 357, market_price: 3.88, market_value: 388 * shortPuts, market_price_is_calculated: false },
    ],
  } as unknown as PortfolioPosition;
}

const IWM_PRICES: Record<string, PriceData> = {
  IWM_20260326_247_C: makePrice("IWM_20260326_247_C", 3.4, 3.46),
  IWM_20260326_243_P: makePrice("IWM_20260326_243_P", 3.8, 3.86),
};

const legRatios = (body: unknown) =>
  ((body as { legs: Array<{ right: string; ratio: number }> }).legs).map((l) => `${l.right}:${l.ratio}`);

describe("OrderTab ComboOrderForm arms for the exact request", () => {
  it("a held-leg change while armed disarms; the re-reviewed legs are what transmit", async () => {
    const calls = recordFetch();
    const before = riskReversal(10, 10);
    const { rerender } = render(
      <OrderTab ticker="IWM" position={before} portfolio={portfolioWith(before)} prices={IWM_PRICES} openOrders={[]} />,
    );
    fireEvent.change(screen.getByDisplayValue("10"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: /MID -0.40/i }));
    await clickWhenEnabled("Place Combo Order");
    expect(screen.getByRole("button", { name: "Confirm Order" })).toBeTruthy();

    // A buy-back of 5 puts lands; the poll delivers legs 10 / 5.
    const after = riskReversal(10, 5);
    rerender(<OrderTab ticker="IWM" position={after} portfolio={portfolioWith(after)} prices={IWM_PRICES} openOrders={[]} />);

    expect(screen.queryByRole("button", { name: "Confirm Order" })).toBeNull();
    expect(places(calls)).toEqual([]);

    await clickWhenEnabled("Place Combo Order");
    await clickWhenEnabled("Confirm Order");
    expect(places(calls)).toHaveLength(1);
    expect(places(calls)[0].method).toBe("POST");
    expect(places(calls)[0].body).toMatchObject({ type: "combo", symbol: "IWM", action: "SELL", quantity: 5, tif: "GTC" });
    expect(legRatios(places(calls)[0].body)).toEqual(["C:2", "P:1"]);
  });
});

describe("PositionTradeTicket arms for the exact request", () => {
  it("a held-leg change while armed disarms; the re-reviewed legs are what transmit", async () => {
    const calls = recordFetch();
    const before = riskReversal(10, 10);
    const { rerender } = render(
      <PositionTradeTicket position={before} prices={IWM_PRICES} portfolio={portfolioWith(before)} target={{ kind: "combo" }} onClose={vi.fn()} />,
    );
    fireEvent.change(screen.getByTestId("position-trade-limit"), { target: { value: "-0.40" } });
    await clickWhenEnabled(/review order/i);
    expect(screen.getByRole("button", { name: /confirm order/i })).toBeTruthy();

    const after = riskReversal(10, 5);
    rerender(
      <PositionTradeTicket position={after} prices={IWM_PRICES} portfolio={portfolioWith(after)} target={{ kind: "combo" }} onClose={vi.fn()} />,
    );

    expect(screen.queryByRole("button", { name: /confirm order/i })).toBeNull();
    expect(places(calls)).toEqual([]);

    // 5 BAG units are held now; the operator re-sizes and reviews again.
    fireEvent.change(screen.getByTestId("position-trade-qty"), { target: { value: "5" } });
    await clickWhenEnabled(/review order/i);
    await clickWhenEnabled(/confirm order/i);
    expect(places(calls)).toHaveLength(1);
    expect(places(calls)[0].method).toBe("POST");
    expect(places(calls)[0].body).toMatchObject({ type: "combo", symbol: "IWM", quantity: 5, limitPrice: -0.4 });
    expect(legRatios(places(calls)[0].body)).toEqual(["C:2", "P:1"]);
  });
});
