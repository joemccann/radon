// @vitest-environment jsdom

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import TickerDetailContent from "../components/TickerDetailContent";
import { TickerDetailProvider } from "../lib/TickerDetailContext";
import type { OrdersData, PortfolioData } from "../lib/types";
import type { PriceData } from "../lib/pricesProtocol";

vi.mock("../components/PriceChart", () => ({
  default: () => React.createElement("div", { "data-testid": "price-chart" }),
}));

vi.mock("../components/QuoteTelemetry", () => ({
  TickerQuoteTelemetry: () => React.createElement("div", { "data-testid": "quote-telemetry" }),
}));

const PRICE: PriceData = {
  symbol: "PLTR",
  last: 153.1,
  lastIsCalculated: false,
  bid: 153.05,
  ask: 153.15,
  bidSize: 100,
  askSize: 100,
  volume: 1000,
  high: null,
  low: null,
  open: null,
  close: 151.5,
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

const PORTFOLIO: PortfolioData = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [],
};

const ORDERS: OrdersData = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

describe("Options chain ATM auto-centering", () => {
  const fetchMock = vi.fn<typeof fetch>();
  const scrollIntoViewSpy = vi.fn();
  const scrollToSpy = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);

    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoViewSpy,
    });
    Object.defineProperty(Element.prototype, "scrollTo", {
      configurable: true,
      value: scrollToSpy,
    });

    fetchMock.mockImplementation((input) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
          ? input.toString()
          : String(input.url);
      if (url.includes("/api/options/expirations")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ symbol: "PLTR", expirations: ["20260327"] }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      if (url.includes("/api/options/chain")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              symbol: "PLTR",
              expiry: "20260327",
              strikes: [148, 150, 152.5, 155, 157.5],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
  });

  afterEach(() => {
    cleanup();
    scrollIntoViewSpy.mockReset();
    scrollToSpy.mockReset();
    vi.restoreAllMocks();
  });

  it("centers the ATM row inside the chain wrapper only — never via scrollIntoView", async () => {
    render(
      React.createElement(
        TickerDetailProvider,
        null,
        React.createElement(TickerDetailContent, {
          ticker: "PLTR",
          activeTab: "chain",
          onTabChange: vi.fn(),
          prices: { PLTR: PRICE },
          fundamentals: {},
          portfolio: PORTFOLIO,
          orders: ORDERS,
          theme: "dark",
        }),
      ),
    );

    await waitFor(() => {
      expect(document.querySelector(".chain-grid-wrapper")).not.toBeNull();
    });

    await waitFor(() => {
      expect(scrollToSpy).toHaveBeenCalled();
    });

    // The ATM row must NEVER call scrollIntoView — that would propagate to
    // page-level scroll containers and drag the Order Builder with it.
    expect(scrollIntoViewSpy).not.toHaveBeenCalled();

    // The scrollTo call must be on the chain wrapper element, not document
    // or some ancestor.
    const wrapper = document.querySelector(".chain-grid-wrapper");
    expect(wrapper).not.toBeNull();
    const calledOnWrapper = scrollToSpy.mock.instances.some(
      (instance) => instance === wrapper,
    );
    expect(calledOnWrapper).toBe(true);
  });
});
