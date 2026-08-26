/**
 * @vitest-environment jsdom
 *
 * FuturesOrderForm renders the shared nine-field quote panel so the operator
 * placing a futures order sees the same telemetry the portfolio position
 * drawer gives them: BID MID ASK / SPREAD LAST / VOLUME HIGH LOW DAY.
 *
 * The only quote reachable for a listed future today is the INDEX quote
 * (prices["VIX"]), not the front-month contract's own feed, so the panel is
 * labelled with the index symbol.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import type { PriceData } from "@/lib/pricesProtocol";

const mocks = vi.hoisted(() => ({
  futuresState: null as unknown,
}));

vi.mock("@/lib/TickerDetailContext", () => ({ useTickerDetailOptional: () => null }));
vi.mock("@/lib/useFuturesChain", () => ({
  useFuturesChain: () => mocks.futuresState,
}));

import { FuturesOrderForm } from "../components/ticker-detail/FuturesOrderForm";

afterEach(cleanup);

const future = {
  conId: 9001,
  symbol: "VIX",
  localSymbol: "VIXU6",
  exchange: "CFE",
  currency: "USD",
  lastTradeDateOrContractMonth: "20260916",
  multiplier: "1000",
  tradingClass: "VX",
  marketName: "VIX",
  minTick: 0.05,
};

function chainLoaded() {
  return {
    data: { symbol: "VIX", exchange: "CFE", contracts: [future], count: 1 },
    loading: false,
    error: null,
  };
}

function vixQuote(): PriceData {
  return {
    symbol: "VIX",
    last: 19.4,
    lastIsCalculated: false,
    bid: 19.3,
    ask: 19.5,
    bidSize: 12,
    askSize: 8,
    volume: 84_120,
    high: 20.1,
    low: 18.9,
    open: 19.0,
    close: 18.8,
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
  } as unknown as PriceData;
}

describe("FuturesOrderForm quote telemetry", () => {
  it("renders the full nine-field panel for the supplied quote", async () => {
    mocks.futuresState = chainLoaded();
    const { container } = render(<FuturesOrderForm ticker="VIX" priceData={vixQuote()} />);

    await waitFor(() => expect(container.querySelector(".price-bar")).toBeTruthy());
    const panel = within(container.querySelector(".price-bar") as HTMLElement);
    for (const label of ["BID", "MID", "ASK", "SPREAD", "LAST", "VOLUME", "HIGH", "LOW", "DAY"]) {
      expect(panel.getByText(label)).toBeTruthy();
    }
    expect(panel.getByText("$19.30")).toBeTruthy();
    expect(panel.getByText("$19.50")).toBeTruthy();
    expect(panel.getByText("84,120")).toBeTruthy();
  });

  it("labels the panel with the index symbol, not the listed contract", async () => {
    mocks.futuresState = chainLoaded();
    render(<FuturesOrderForm ticker="VIX" priceData={vixQuote()} />);

    await waitFor(() => expect(screen.getByText("VIX Index")).toBeTruthy());
    expect(screen.queryByText("VIXU6 Index")).toBeNull();
  });

  it("falls back to the empty panel when no quote is threaded", async () => {
    mocks.futuresState = chainLoaded();
    const { container } = render(<FuturesOrderForm ticker="VIX" />);

    await waitFor(() => expect(container.querySelector(".price-bar-empty")).toBeTruthy());
    expect(container.querySelectorAll(".price-bar-label").length).toBe(0);
  });
});
