/** @vitest-environment jsdom */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import TickerDetailContent from "@/components/TickerDetailContent";
import { OrderActionsProvider } from "@/lib/OrderActionsContext";
import { TickerDetailProvider } from "@/lib/TickerDetailContext";
import type { DepthBook, PriceData } from "@/lib/pricesProtocol";
import type { OrdersData, PortfolioData, PortfolioPosition } from "@/lib/types";

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true }),
}));

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn() }),
}));

vi.mock("@/lib/useStockState", () => ({
  useStockState: () => ({ fallback: null }),
}));

vi.mock("@/components/ticker-detail/FuturesOrderForm", () => ({
  FuturesOrderForm: () => React.createElement("div", { "data-testid": "futures-order-form" }),
}));

vi.mock("@/components/ticker-detail/IndexOptionOrderForm", () => ({
  IndexOptionOrderForm: () => React.createElement("div", { "data-testid": "index-option-order-form" }),
}));

const timestamp = "2026-09-04T18:55:00.000Z";
const expiry = "2026-09-09";
const longKey = "VIX_20260909_20_C";
const shortKey = "VIX_20260909_30_C";

function price(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol,
    last: (bid + ask) / 2,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 10,
    askSize: 10,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
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

function depth(symbol: string, bid: number, ask: number, bidSize: number, askSize: number): DepthBook {
  return {
    symbol,
    kind: "option",
    bid: [{ price: bid, size: bidSize, marketMaker: null, exchange: "CBOE", nbbo: true }],
    ask: [{ price: ask, size: askSize, marketMaker: null, exchange: "CBOE", nbbo: true }],
    isSmartDepth: true,
    feed: "OPRA BBO",
    entitled: true,
    timestamp,
  };
}

const position: PortfolioPosition = {
  id: 9,
  ticker: "VIX",
  structure: "Ratio Bull Call Spread 500x499 $20/$30",
  structure_type: "Ratio Bull Call Spread",
  risk_profile: "undefined",
  expiry,
  contracts: 500,
  direction: "DEBIT",
  entry_cost: 40_000,
  max_risk: null,
  market_value: 39_062,
  market_price_is_calculated: true,
  legs: [
    { direction: "LONG", contracts: 500, type: "Call", strike: 20, entry_cost: 70_000, avg_cost: 140, market_price: 1.40, market_value: 70_000 },
    { direction: "SHORT", contracts: 499, type: "Call", strike: 30, entry_cost: 30_000, avg_cost: 60.12, market_price: 0.62, market_value: 30_938 },
  ],
};

const portfolio = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: timestamp,
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 1,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  positions: [position],
} as PortfolioData;

const orders: OrdersData = {
  last_sync: timestamp,
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("VIX imbalanced option spread book", () => {
  it("shows the per-contract implied spread and follows each focused option book", async () => {
    render(
      <OrderActionsProvider>
        <TickerDetailProvider>
          <TickerDetailContent
            ticker="VIX"
            positionId={9}
            activeTab="book"
            onTabChange={vi.fn()}
            prices={{
              VIX: price("VIX", 18.90, 18.92),
              [longKey]: price(longKey, 1.39, 1.41),
              [shortKey]: price(shortKey, 0.61, 0.63),
            }}
            fundamentals={{}}
            portfolio={portfolio}
            orders={orders}
            depths={{
              [longKey]: depth(longKey, 1.39, 1.41, 50, 8_933),
              [shortKey]: depth(shortKey, 0.61, 0.63, 999, 4_566),
            }}
            tape={{}}
            theme="dark"
          />
        </TickerDetailProvider>
      </OrderActionsProvider>,
    );

    const cockpitHeader = document.querySelector(".cockpit-head") as HTMLElement;
    const bookWindow = document.querySelector(".book-window") as HTMLElement;
    const implied = screen.getByRole("button", { name: "Implied spread book" });
    const long = screen.getByRole("button", { name: "$20 Call book" });
    const short = screen.getByRole("button", { name: "$30 Call book" });

    expect(implied.getAttribute("aria-pressed")).toBe("true");
    expect(document.querySelector(".book-kind")?.textContent).toBe("IMPLIED SPREAD");
    expect(cockpitHeader.textContent).toContain("$0.78");
    expect(cockpitHeader.textContent).toContain("NET $0.04 / 5.13%");
    expect(bookWindow.textContent).toContain("BID$0.76");
    expect(bookWindow.textContent).toContain("ASK$0.80");
    expect(bookWindow.textContent).not.toContain("390.62");

    fireEvent.click(long);
    await waitFor(() => expect(long.getAttribute("aria-pressed")).toBe("true"));
    expect(document.querySelector(".book-kind")?.textContent).toBe("OPTION");
    expect(cockpitHeader.textContent).toContain("$1.40");
    expect(cockpitHeader.textContent).toContain("SPREAD $0.02 / 1.43%");

    fireEvent.click(short);
    await waitFor(() => expect(short.getAttribute("aria-pressed")).toBe("true"));
    expect(cockpitHeader.textContent).toContain("$0.62");
    expect(cockpitHeader.textContent).toContain("SPREAD $0.02 / 3.23%");

    fireEvent.click(implied);
    await waitFor(() => expect(implied.getAttribute("aria-pressed")).toBe("true"));
    expect(document.querySelector(".book-kind")?.textContent).toBe("IMPLIED SPREAD");
    expect(cockpitHeader.textContent).toContain("$0.78");
    expect(cockpitHeader.textContent).toContain("NET $0.04 / 5.13%");
  });
});
