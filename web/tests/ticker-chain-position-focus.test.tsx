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

const PLTR_PRICE: PriceData = {
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
  total_deployed_pct: 2,
  total_deployed_dollars: 2000,
  remaining_capacity_pct: 98,
  position_count: 1,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [
    {
      id: 16,
      ticker: "PLTR",
      structure: "Risk Reversal (P$152.5/C$155.0)",
      structure_type: "Risk Reversal",
      risk_profile: "undefined",
      expiry: "2026-03-27",
      contracts: 20,
      direction: "COMBO",
      entry_cost: -1571.92,
      max_risk: null,
      market_value: -320,
      market_price_is_calculated: false,
      ib_daily_pnl: null,
      legs: [
        {
          direction: "LONG",
          contracts: 20,
          type: "Call",
          strike: 155,
          entry_cost: 5034.01,
          avg_cost: 251.7,
          market_price: 2.82,
          market_value: 5640,
          market_price_is_calculated: false,
        },
        {
          direction: "SHORT",
          contracts: 20,
          type: "Put",
          strike: 152.5,
          entry_cost: 6605.93,
          avg_cost: 330.29,
          market_price: 2.98,
          market_value: 5960,
          market_price_is_calculated: false,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-24",
    },
  ],
};

const ORDERS: OrdersData = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

describe("Ticker chain position focus", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    if (!("scrollIntoView" in HTMLElement.prototype)) {
      Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
        configurable: true,
        value: vi.fn(),
      });
    } else {
      vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(() => {});
    }

    fetchMock.mockImplementation((input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : String(input.url);
      if (url.includes("/api/options/expirations")) {
        return Promise.resolve(
          new Response(JSON.stringify({ symbol: "PLTR", expirations: ["20260327", "20260417"] }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      if (url.includes("/api/options/chain") && url.includes("expiry=20260327")) {
        return Promise.resolve(
          new Response(JSON.stringify({ symbol: "PLTR", expiry: "20260327", strikes: [150, 152.5, 155] }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      if (url.includes("/api/options/chain") && url.includes("expiry=20260417")) {
        return Promise.resolve(
          new Response(JSON.stringify({ symbol: "PLTR", expiry: "20260417", strikes: [150, 152.5, 155] }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
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
      throw new Error(`Unexpected fetch: ${url}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("uses the deep-linked position expiry instead of the generic >=7d default on the chain tab", async () => {
    render(
      React.createElement(
        TickerDetailProvider,
        null,
        React.createElement(TickerDetailContent, {
          ticker: "PLTR",
          positionId: 16,
          activeTab: "chain",
          onTabChange: vi.fn(),
          prices: { PLTR: PLTR_PRICE },
          fundamentals: {},
          portfolio: PORTFOLIO,
          orders: ORDERS,
          theme: "dark",
        }),
      ),
    );

    const expirySelect = (await screen.findAllByRole("combobox"))[0] as HTMLSelectElement;
    await waitFor(() => {
      expect(expirySelect.value).toBe("20260327");
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/options/chain?symbol=PLTR&expiry=20260327");
    });
  });
});
