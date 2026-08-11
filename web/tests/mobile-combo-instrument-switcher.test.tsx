/**
 * @vitest-environment jsdom
 *
 * Mobile ticker detail: multi-leg equity option positions still need the
 * STOCK|OPTION switcher. A ratio risk reversal defaults to the held option
 * position's net quote; the user can switch to the underlying stock book from
 * the same mobile header.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import TickerDetailContent from "../components/TickerDetailContent";
import { OrderActionsProvider } from "../lib/OrderActionsContext";
import { TickerDetailProvider } from "../lib/TickerDetailContext";
import type { DepthBook, PriceData } from "../lib/pricesProtocol";
import type { OrdersData, PortfolioData, PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/EWY",
  useRouter: () => ({
    replace: vi.fn(),
    push: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: true, hasMounted: true }),
}));

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({
    watchlist: [],
    isLoading: false,
    isWatched: () => false,
    toggleWatch: vi.fn(),
  }),
}));

const now = "2026-06-23T20:30:00.000Z";

function price(symbol: string, over: Partial<PriceData>): PriceData {
  return {
    symbol,
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
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
    timestamp: now,
    ...over,
  };
}

const EWY_RRR: PortfolioPosition = {
  id: 77,
  ticker: "EWY",
  structure: "Ratio Reverse Risk Reversal 5x30 (P$180.0/C$215.0)",
  structure_type: "Ratio Reverse Risk Reversal",
  risk_profile: "undefined",
  expiry: "20260620",
  contracts: 5,
  direction: "LONG",
  entry_cost: 6687,
  max_risk: null,
  market_value: 17037,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-06-01",
  legs: [
    {
      direction: "LONG",
      type: "Put",
      strike: 180,
      contracts: 5,
      entry_cost: 6000,
      avg_cost: 12,
      market_price: 10.5,
      market_value: 5250,
    },
    {
      direction: "SHORT",
      type: "Call",
      strike: 215,
      contracts: 30,
      entry_cost: 1200,
      avg_cost: 0.4,
      market_price: 1.1,
      market_value: 3300,
    },
  ],
};

const PORTFOLIO: PortfolioData = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: now,
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 1,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  positions: [EWY_RRR],
};

const ORDERS: OrdersData = {
  last_sync: now,
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const STOCK_DEPTH: DepthBook = {
  symbol: "EWY",
  kind: "stock",
  isSmartDepth: true,
  feed: "SMART DEPTH",
  entitled: true,
  timestamp: now,
  bid: [{ price: 194.1, size: 53, marketMaker: "IBEOS", exchange: "SMART" }],
  ask: [{ price: 194.12, size: 25, marketMaker: "IBEOS", exchange: "SMART" }],
};

const PUT_DEPTH: DepthBook = {
  symbol: "EWY_20260620_180_P",
  kind: "option",
  isSmartDepth: true,
  feed: "OPRA BBO",
  entitled: true,
  timestamp: now,
  bid: [{ price: 10, size: 12, marketMaker: "CBOE", exchange: "CBOE" }],
  ask: [{ price: 11, size: 8, marketMaker: "BOX", exchange: "BOX" }],
};

function renderMobileCombo(onDepthSymbolChange = vi.fn()) {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : String(input.url);
    if (url.includes("/api/ticker/info")) {
      return Promise.resolve(
        new Response(JSON.stringify({ stock_state: {}, uw_info: {}, profile: {}, stats: {} }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
    return Promise.reject(new Error(`Unexpected fetch: ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  return render(
    <OrderActionsProvider>
      <TickerDetailProvider>
        <TickerDetailContent
          ticker="EWY"
          positionId={77}
          activeTab="book"
          onTabChange={vi.fn()}
          prices={{
            EWY: price("EWY", { last: 194.11, bid: 194.1, ask: 194.12, close: 192.2 }),
            EWY_20260620_180_P: price("EWY_20260620_180_P", { bid: 10, ask: 11 }),
            EWY_20260620_215_C: price("EWY_20260620_215_C", { bid: 1, ask: 1.2 }),
          }}
          fundamentals={{}}
          portfolio={PORTFOLIO}
          orders={ORDERS}
          depths={{ EWY: STOCK_DEPTH, EWY_20260620_180_P: PUT_DEPTH }}
          tape={{}}
          onDepthSymbolChange={onDepthSymbolChange}
          theme="dark"
        />
      </TickerDetailProvider>
    </OrderActionsProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("TickerDetailContent mobile — combo instrument switcher", () => {
  it("shows STOCK|OPTION for a ratio risk reversal and keeps OPTION on the spread quote", async () => {
    const onDepthSymbolChange = vi.fn();
    renderMobileCombo(onDepthSymbolChange);

    const group = await screen.findByRole("group", { name: "Instrument view" });
    const stock = screen.getByRole("button", { name: "STOCK" });
    const option = screen.getByRole("button", { name: "OPTION" });

    expect(group).toBeTruthy();
    expect(option.getAttribute("aria-pressed")).toBe("true");
    expect(stock.getAttribute("aria-pressed")).toBe("false");
    expect(option.classList.contains("on")).toBe(true);
    expect(stock.classList.contains("on")).toBe(false);

    const header = document.querySelector(".ckh--mobile") as HTMLElement;
    expect(header.textContent ?? "").toContain("9.40");
    expect(header.textContent ?? "").not.toContain("194.11");

    await waitFor(() => {
      expect(onDepthSymbolChange).toHaveBeenCalledWith("EWY_20260620_180_P");
    });
    expect(screen.getByRole("group", { name: "Option leg book" })).toBeTruthy();
    const putBook = screen.getByRole("button", { name: "$180 Put book" });
    const callBook = screen.getByRole("button", { name: "$215 Call book" });
    expect(putBook.getAttribute("aria-pressed")).toBe("true");
    expect(document.querySelector(".book-sym")?.textContent ?? "").toContain("EWY $180P");
    expect(document.querySelector(".book-window")?.textContent ?? "").toContain("OPRA BBO");
    expect(document.querySelector(".book-window")?.textContent ?? "").not.toContain("IBEOS");

    fireEvent.click(callBook);
    await waitFor(() => {
      expect(onDepthSymbolChange).toHaveBeenCalledWith("EWY_20260620_215_C");
    });
    expect(callBook.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(stock);
    await waitFor(() => expect(stock.getAttribute("aria-pressed")).toBe("true"));
    await waitFor(() => expect(onDepthSymbolChange).toHaveBeenCalledWith("EWY"));
    expect(option.getAttribute("aria-pressed")).toBe("false");
    expect(stock.classList.contains("on")).toBe(true);
    expect(option.classList.contains("on")).toBe(false);

    fireEvent.click(option);
    await waitFor(() => expect(onDepthSymbolChange).toHaveBeenCalledWith("EWY_20260620_215_C"));
    expect(callBook.getAttribute("aria-pressed")).toBe("true");
  });
});
