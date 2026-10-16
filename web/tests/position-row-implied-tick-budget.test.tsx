/**
 * @vitest-environment jsdom
 *
 * PERF-013: a live tick for one symbol must not rerun Black-Scholes for
 * every other row. The relay replaces one PriceData entry and keeps the
 * rest by reference (`usePrices` spread). PositionRow used the whole
 * `prices` object as a dep, so one SPY tick recomputed implied value for
 * every option in the book.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

import PositionTable from "../components/PositionTable";
import MobilePositionList from "../components/mobile/MobilePositionList";
import { seedRiskFreeRateForTests } from "@/lib/useRiskFreeRate";
import * as impliedValue from "@/lib/impliedValue";
import type { PortfolioPosition } from "../lib/types";
import type { PriceData } from "../lib/pricesProtocol";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(cleanup);

beforeEach(() => {
  seedRiskFreeRateForTests(0);
  window.localStorage.clear();
});

const EXPIRY = "2099-05-01";
const EXPIRY_COMPACT = "20990501";

function pd(over: Partial<PriceData>): PriceData {
  return {
    symbol: "X",
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
    timestamp: "2026-10-15T20:00:00.000Z",
    ...over,
  };
}

function optionPosition(id: number, ticker: string): PortfolioPosition {
  return {
    id,
    ticker,
    structure: `Long Put $295`,
    structure_type: "Long Put",
    risk_profile: "defined",
    expiry: EXPIRY,
    contracts: 10,
    direction: "LONG",
    entry_cost: 3000,
    max_risk: 3000,
    market_value: null,
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-04-25",
    legs: [
      {
        direction: "LONG",
        contracts: 10,
        type: "Put",
        strike: 295,
        entry_cost: 3000,
        avg_cost: 3.0,
        market_price: 3.0,
        market_value: 3000,
      },
    ],
  };
}

function bookPrices(tickers: string[]): Record<string, PriceData> {
  const prices: Record<string, PriceData> = {};
  for (const ticker of tickers) {
    prices[ticker] = pd({ symbol: ticker, last: 280 });
    prices[`${ticker}_${EXPIRY_COMPACT}_295_P`] = pd({
      symbol: `${ticker}_${EXPIRY_COMPACT}_295_P`,
      impliedVol: 0.45,
    });
  }
  return prices;
}

const ROW_COUNT = 8;

describe("PERF-013 position implied-value tick budget", () => {
  it("desktop: an unrelated symbol tick adds zero computePositionImpliedValue calls", () => {
    const positions = Array.from({ length: ROW_COUNT }, (_, i) =>
      optionPosition(i + 1, `T${i}`),
    );
    const prices = bookPrices(positions.map((p) => p.ticker));
    const spy = vi.spyOn(impliedValue, "computePositionImpliedValue");
    const { rerender } = render(<PositionTable positions={positions} prices={prices} />);
    const afterMount = spy.mock.calls.length;
    expect(afterMount).toBeGreaterThan(0);

    rerender(
      <PositionTable
        positions={positions}
        prices={{ ...prices, SPY: pd({ symbol: "SPY", last: 500 }) }}
      />,
    );
    expect(spy.mock.calls.length - afterMount).toBe(0);
    spy.mockRestore();
  });

  it("desktop: a tick for one held ticker recomputes only that row", () => {
    const positions = Array.from({ length: ROW_COUNT }, (_, i) =>
      optionPosition(i + 1, `T${i}`),
    );
    const prices = bookPrices(positions.map((p) => p.ticker));
    const spy = vi.spyOn(impliedValue, "computePositionImpliedValue");
    const { rerender } = render(<PositionTable positions={positions} prices={prices} />);
    const afterMount = spy.mock.calls.length;

    rerender(
      <PositionTable
        positions={positions}
        prices={{ ...prices, T0: pd({ symbol: "T0", last: 281 }) }}
      />,
    );
    const extra = spy.mock.calls.slice(afterMount);
    expect(new Set(extra.map((call) => call[0].ticker))).toEqual(new Set(["T0"]));
    spy.mockRestore();
  });

  it("mobile: an unrelated symbol tick adds zero computePositionImpliedValue calls", () => {
    const positions = Array.from({ length: ROW_COUNT }, (_, i) =>
      optionPosition(i + 1, `T${i}`),
    );
    const prices = bookPrices(positions.map((p) => p.ticker));
    const spy = vi.spyOn(impliedValue, "computePositionImpliedValue");
    const { rerender } = render(
      <MobilePositionList positions={positions} prices={prices} />,
    );
    const afterMount = spy.mock.calls.length;
    expect(afterMount).toBeGreaterThan(0);

    rerender(
      <MobilePositionList
        positions={positions}
        prices={{ ...prices, SPY: pd({ symbol: "SPY", last: 500 }) }}
      />,
    );
    expect(spy.mock.calls.length - afterMount).toBe(0);
    spy.mockRestore();
  });
});
