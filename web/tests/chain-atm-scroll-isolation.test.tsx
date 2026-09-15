// @vitest-environment jsdom

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

// OptionsChainTab deep-links its filters via useChainUrlState (next/navigation).
// Provide a no-op router so useRouter() doesn't throw "app router not mounted".
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/test",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn() }),
}));

import TickerDetailContent from "../components/TickerDetailContent";
import { TickerDetailProvider } from "../lib/TickerDetailContext";
import { OrderActionsProvider } from "../lib/OrderActionsContext";
import type { OrdersData, PortfolioData } from "../lib/types";
import type { PriceData } from "../lib/pricesProtocol";

vi.mock("../components/PriceChart", () => ({
  default: () => React.createElement("div", { "data-testid": "price-chart" }),
}));

vi.mock("../components/QuoteTelemetry", () => ({
  TickerQuoteTelemetry: () => React.createElement("div", { "data-testid": "quote-telemetry" }),
  OrderQuoteTelemetry: () => React.createElement("div", { "data-testid": "order-quote-telemetry" }),
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
      // useRiskFreeRate (called from OptionsChainTab to drive Implied col)
      if (url.includes("/api/risk-free-rate")) {
        return Promise.resolve(
          new Response(JSON.stringify({ rate: 0 }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      // TickerDetailContent fetches stock-state for the after-hours quote-bar fallback.
      if (url.includes("/api/ticker/info")) {
        return Promise.resolve(
          new Response(JSON.stringify({ stock_state: {}, uw_info: {}, profile: {}, stats: {} }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
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

  it("resets only the independent panes and preserves browsing across live strike crossings", async () => {
    const content = (last: number) => (
      React.createElement(
        OrderActionsProvider,
        null,
        React.createElement(
          TickerDetailProvider,
          null,
          React.createElement(TickerDetailContent, {
            ticker: "PLTR",
            activeTab: "c", // deck key for Chain (cockpit nav contract; opens the Chain deck)
            onTabChange: vi.fn(),
            prices: { PLTR: { ...PRICE, last } },
            fundamentals: {},
            portfolio: PORTFOLIO,
            orders: ORDERS,
            theme: "dark",
          }),
        ),
      )
    );
    const { rerender } = render(content(153.1));

    await waitFor(() => {
      expect(document.querySelector(".chain-grid-wrapper")).not.toBeNull();
    });

    expect(scrollIntoViewSpy).not.toHaveBeenCalled();
    const upper = screen.getByTestId("chain-upper-pane");
    const lower = screen.getByTestId("chain-lower-pane");
    expect(scrollToSpy.mock.instances.every((instance) => instance === upper || instance === lower)).toBe(true);
    upper.scrollTop = 40;
    lower.scrollTop = 70;
    fireEvent.scroll(upper);
    fireEvent.scroll(lower);
    const strikes = (pane: HTMLElement) => Array.from(pane.querySelectorAll(".chain-strike"), (cell) => cell.textContent);
    const before = { upper: strikes(upper), lower: strikes(lower) };
    scrollToSpy.mockClear();
    rerender(content(156));
    await waitFor(() => expect(screen.getByTestId("chain-spot-bar").textContent).toContain("156.00"));
    expect(strikes(upper)).toEqual(before.upper);
    expect(strikes(lower)).toEqual(before.lower);
    expect(upper.scrollTop).toBe(40);
    expect(lower.scrollTop).toBe(70);
    expect(scrollToSpy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Recenter options chain", exact: true }));
    await waitFor(() => expect(lower.scrollTop).toBe(0));
    expect(upper.scrollTop).toBe(upper.scrollHeight);
    expect(strikes(upper)).not.toEqual(before.upper);
    expect(scrollToSpy.mock.instances.every((instance) => instance === upper || instance === lower)).toBe(true);
    expect(scrollIntoViewSpy).not.toHaveBeenCalled();
  });
});
