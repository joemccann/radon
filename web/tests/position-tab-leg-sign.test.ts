/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import PositionTab from "../components/ticker-detail/PositionTab";
import type { PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

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

describe("PositionTab leg sign display", () => {
  afterEach(() => cleanup());

  it("uses a live combo mark in the summary instead of stale leg last trades", () => {
    render(React.createElement(PositionTab, { position: POSITION, prices: {
      IWM_20260326_247_C: {
        ...PRICES.IWM_20260326_247_C,
        bid: 3.25,
        ask: 3.28,
        last: 3.26,
      },
      IWM_20260326_243_P: {
        ...PRICES.IWM_20260326_243_P,
        bid: 3.00,
        ask: 3.02,
        last: 3.51,
      },
    } }));

    expect(screen.getByText("Mark Price")).toBeTruthy();
    expect(screen.getByText("$0.26")).toBeTruthy();
    expect(screen.queryByText("-$0.25")).toBeNull();
  });

  it("renders short legs as negative/red and long legs as positive/green in the legs table", () => {
    render(React.createElement(PositionTab, { position: POSITION, prices: PRICES }));

    fireEvent.click(screen.getByRole("button", { name: /Legs \(2\)/i }));

    const longRow = screen.getByText("LONG").closest("tr");
    expect(longRow?.textContent).toContain("$3.46");
    // last=3.63 > ask=3.46 → resolveRealtimePrice uses mid=(3.40+3.46)/2=3.43
    expect(longRow?.textContent).toContain("$3.43");
    expect(longRow?.querySelector("td.positive")).not.toBeNull();

    const shortRow = screen.getByText("SHORT").closest("tr");
    expect(shortRow?.textContent).toContain("-$3.57");
    // last=3.88 > ask=3.86 → resolveRealtimePrice uses mid=(3.80+3.86)/2=3.83
    expect(shortRow?.textContent).toContain("-$3.83");
    expect(shortRow?.querySelector("td.negative")).not.toBeNull();
  });
});
