/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import BookTab from "@/components/ticker-detail/BookTab";
import { TickerDetailProvider } from "@/lib/TickerDetailContext";
import type { DepthBook, PriceData } from "@/lib/pricesProtocol";
import type { PortfolioPosition } from "@/lib/types";

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true }),
}));

const timestamp = "2026-08-11T16:00:00.000Z";
const keys = [50, 55, 60, 65].map((strike) => `TEST_20260918_${strike}_C`);

function price(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol, bid, ask, last: (bid + ask) / 2, lastIsCalculated: true,
    bidSize: 10, askSize: 10, volume: null, high: null, low: null, open: null,
    close: null, week52High: null, week52Low: null, avgVolume: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null,
    undPrice: null, timestamp,
  };
}

function depth(symbol: string, bid: number, ask: number): DepthBook {
  return {
    symbol, kind: "option", entitled: true, isSmartDepth: true, feed: "OPRA BBO",
    timestamp,
    bid: [{ price: bid, size: 10, marketMaker: null, exchange: "CBOE", nbbo: true }],
    ask: [{ price: ask, size: 10, marketMaker: null, exchange: "BOX", nbbo: true }],
  };
}

const position: PortfolioPosition = {
  id: 1, ticker: "TEST", structure: "Iron Condor", structure_type: "Iron Condor",
  risk_profile: "defined", expiry: "2026-09-18", contracts: 10, direction: "COMBO",
  entry_cost: -2_000, max_risk: 3_000, market_value: -2_000,
  kelly_optimal: null, target: null, stop: null, entry_date: "2026-08-01",
  legs: keys.map((_, index) => ({
    direction: index < 2 ? "LONG" as const : "SHORT" as const,
    contracts: 10, type: "Call" as const, strike: [50, 55, 60, 65][index],
    entry_cost: 500, avg_cost: 50, market_price: 1, market_value: 1_000,
  })),
};

afterEach(cleanup);

describe("generalized implied combo book rendering", () => {
  it("renders a signed negative four-leg hybrid book without absolute-value coercion", () => {
    const prices = Object.fromEntries(keys.map((key, index) => [
      key,
      price(key, index < 2 ? 1 : 2, index < 2 ? 1.1 : 2.1),
    ]));
    const depths = Object.fromEntries(keys.slice(0, 3).map((key, index) => [
      key,
      depth(key, index < 2 ? 1 : 2, index < 2 ? 1.1 : 2.1),
    ]));

    render(
      <TickerDetailProvider>
        <BookTab
          ticker="TEST"
          position={position}
          prices={prices}
          openOrders={[]}
          tickerPriceData={price("TEST-COMBO", -2.2, -1.8)}
          depths={depths}
          tape={{}}
          bookKey={keys.join("+")}
          bookKind="combo"
          bookOnly
        />
      </TickerDetailProvider>,
    );

    expect(screen.getByText("HYBRID IMPLIED · 3 DEPTH + 1 BBO")).toBeTruthy();
    expect(document.querySelector(".book-window")?.textContent).toContain("-2.20");
    expect(document.querySelector(".book-window")?.textContent).toContain("-1.80");
    expect(screen.queryByRole("button", { name: "Toggle Time and Sales" })).toBeNull();
  });

  it("keeps a multi-venue combo row on one line — leading pair plus an overflow count", () => {
    const spreadKeys = [keys[0], keys[1]];
    const multiVenue = (
      symbol: string,
      bid: number,
      ask: number,
      bidVenues: string[],
      askVenues: string[],
    ): DepthBook => ({
      symbol, kind: "option", entitled: true, isSmartDepth: true, feed: "OPRA BBO", timestamp,
      bid: bidVenues.map((exchange, index) => ({
        price: bid, size: 10, marketMaker: null, exchange, nbbo: index === 0,
      })),
      ask: askVenues.map((exchange, index) => ({
        price: ask, size: 10, marketMaker: null, exchange, nbbo: index === 0,
      })),
    });

    const spread: PortfolioPosition = {
      ...position,
      structure: "Bull Call Spread",
      structure_type: "Bull Call Spread",
      legs: [
        { ...position.legs[0], direction: "LONG", strike: 50 },
        { ...position.legs[1], direction: "SHORT", strike: 55 },
      ],
    };

    render(
      <TickerDetailProvider>
        <BookTab
          ticker="TEST"
          position={spread}
          prices={{
            [spreadKeys[0]]: price(spreadKeys[0], 1, 1.1),
            [spreadKeys[1]]: price(spreadKeys[1], 0.5, 0.6),
          }}
          openOrders={[]}
          tickerPriceData={price("TEST-COMBO", 0.4, 0.6)}
          depths={{
            [spreadKeys[0]]: multiVenue(spreadKeys[0], 1, 1.1, ["AMEX", "BATS", "CBOE"], ["AMEX", "BATS", "CBOE"]),
            [spreadKeys[1]]: multiVenue(spreadKeys[1], 0.5, 0.6, ["EDGX", "MIAX", "PHLX"], ["EDGX", "MIAX", "PHLX"]),
          }}
          tape={{}}
          bookKey={spreadKeys.join("+")}
          bookKind="combo"
          bookOnly
        />
      </TickerDetailProvider>,
    );

    const venueCell = document.querySelector(".book-side.bid .book-row .book-mkt");
    expect(venueCell).toBeTruthy();
    // One venue pair on the row, never the whole pipe-joined set (which wraps
    // into a 30-line block and blows the row height out of the montage).
    expect(venueCell?.textContent).not.toContain("|");
    expect(venueCell?.textContent).toContain("AMEX / EDGX");
    expect(venueCell?.querySelector(".book-venue-more")?.textContent).toBe("+2");
    expect(venueCell?.getAttribute("title")).toContain("|");
  });
});
