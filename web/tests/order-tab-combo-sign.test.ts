/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import OrderTab from "../components/ticker-detail/OrderTab";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

const mockRequestModify = vi.fn();

vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({
    pendingCancels: new Map(),
    pendingModifies: new Map(),
    cancelledOrders: [],
    requestCancel: vi.fn(),
    requestModify: mockRequestModify,
    drainNotifications: vi.fn(() => []),
    setOrdersUpdater: vi.fn(),
  }),
}));

vi.mock("@/components/ModifyOrderModal", () => ({
  default: () => null,
}));

const POSITION: PortfolioPosition = {
  id: 12,
  ticker: "IWM",
  structure: "Risk Reversal (P$243.0/C$247.0)",
  structure_type: "Risk Reversal",
  risk_profile: "undefined",
  expiry: "2026-03-26",
  contracts: 50,
  direction: "COMBO",
  entry_cost: -579.79,
  max_risk: null,
  market_value: 750,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-03-19",
  legs: [
    {
      direction: "LONG",
      contracts: 50,
      type: "Call",
      strike: 247,
      entry_cost: 17285.02,
      avg_cost: 346,
      market_price: 3.63,
      market_value: 18150,
      market_price_is_calculated: false,
    },
    {
      direction: "SHORT",
      contracts: 50,
      type: "Put",
      strike: 243,
      entry_cost: 17864.81,
      avg_cost: 357,
      market_price: 3.88,
      market_value: 19400,
      market_price_is_calculated: false,
    },
  ],
};

const PORTFOLIO: PortfolioData = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 1,
  total_deployed_dollars: 1_000,
  remaining_capacity_pct: 99,
  position_count: 1,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  positions: [POSITION],
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 100_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 200_000,
    dividends: 0,
  },
};

const PRICES: Record<string, PriceData> = {
  IWM_20260326_247_C: {
    symbol: "IWM_20260326_247_C",
    last: 3.63,
    lastIsCalculated: false,
    bid: 3.4,
    ask: 3.46,
    bidSize: 1,
    askSize: 1,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 3.61,
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
  },
  IWM_20260326_243_P: {
    symbol: "IWM_20260326_243_P",
    last: 3.88,
    lastIsCalculated: false,
    bid: 3.8,
    ask: 3.86,
    bidSize: 1,
    askSize: 1,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 3.84,
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
  },
};

describe("OrderTab combo sign handling", () => {
  beforeEach(() => {
    mockRequestModify.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("preserves negative combo quotes in the strip and net limit input", () => {
    const { container, getByRole } = render(
      React.createElement(OrderTab, {
        ticker: "IWM",
        position: POSITION,
        portfolio: PORTFOLIO,
        prices: PRICES,
        openOrders: [],
      }),
    );

    // The strip shows the structural fair value of the spread, sign-preserved
    // and matching the InstrumentDetail header. For this Risk Reversal:
    //   netBid = LONG call.bid - SHORT put.bid = 3.40 - 3.80 = -0.40
    //   netAsk = LONG call.ask - SHORT put.ask = 3.46 - 3.86 = -0.40
    const strip = container.querySelector(".spread-price-strip");
    expect(strip?.textContent).toContain("-$0.40");

    fireEvent.click(getByRole("button", { name: /MID -0.40/i }));

    const input = container.querySelector(".modify-price-input") as HTMLInputElement | null;
    expect(input?.value).toBe("-0.40");

    const submit = getByRole("button", { name: "Place Combo Order" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(false);
  });

  it("uses close-order labels instead of max-gain language for a held combo sell", () => {
    const { container, getByRole, queryByText } = render(
      React.createElement(OrderTab, {
        ticker: "IWM",
        position: POSITION,
        portfolio: PORTFOLIO,
        prices: PRICES,
        openOrders: [],
      }),
    );

    const input = container.querySelector(".modify-price-input") as HTMLInputElement | null;
    expect(input).not.toBeNull();
    fireEvent.change(input!, { target: { value: "3.00" } });

    fireEvent.click(getByRole("button", { name: "Place Combo Order" }));

    expect(queryByText("Max Gain:")).toBeNull();
    expect(queryByText("Max Loss:")).toBeNull();
    expect(queryByText("Close Credit:")).not.toBeNull();
    expect(queryByText("$15,000")).not.toBeNull();
    expect(queryByText("Est. Realized P&L:")).not.toBeNull();
    expect(queryByText("$15,580")).not.toBeNull();
  });
});
