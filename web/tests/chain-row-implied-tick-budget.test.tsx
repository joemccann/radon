/**
 * @vitest-environment jsdom
 *
 * PERF-032: a live tick for one chain contract must not rerun Black-Scholes
 * for every other strike. The relay replaces one PriceData entry and keeps
 * the rest by reference (`usePrices` spread). StrikeRow used the whole
 * `prices` object as a dep, so one option tick recomputed implied value for
 * every visible call and put.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/IWO",
  useRouter: () => ({
    replace: vi.fn(),
    push: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
}));

import OptionsChainTab from "../components/ticker-detail/OptionsChainTab";
import { TickerDetailProvider } from "../lib/TickerDetailContext";
import { seedRiskFreeRateForTests } from "@/lib/useRiskFreeRate";
import * as impliedValue from "@/lib/impliedValue";
import { optionKey } from "@/lib/pricesProtocol";
import type { PriceData } from "@/lib/pricesProtocol";
import { __resetUiPreferencesForTests } from "@/lib/uiPreferences";

const TICKER = "IWO";
const EXPIRY = "20991231";
const SPOT = 352.85;
const STRIKES = [340, 345, 350, 355, 360, 365, 370, 375];
const ROW_COUNT = STRIKES.length;

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
    timestamp: "2026-10-27T20:00:00.000Z",
    ...over,
  };
}

function optionPd(right: "C" | "P", strike: number): PriceData {
  return pd({
    symbol: optionKey({ symbol: TICKER, expiry: EXPIRY, strike, right }),
    bid: 9.4,
    ask: 12.2,
    last: 10.8,
    impliedVol: 0.199,
    undPrice: SPOT,
  });
}

function bookPrices(): Record<string, PriceData> {
  const prices: Record<string, PriceData> = {
    [TICKER]: pd({ symbol: TICKER, last: SPOT }),
  };
  for (const strike of STRIKES) {
    prices[optionKey({ symbol: TICKER, expiry: EXPIRY, strike, right: "C" })] = optionPd("C", strike);
    prices[optionKey({ symbol: TICKER, expiry: EXPIRY, strike, right: "P" })] = optionPd("P", strike);
  }
  return prices;
}

function installFetchMock() {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>((input) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : (input as Request).url;
    const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    if (url.includes("/api/options/expirations")) return json({ symbol: TICKER, expirations: [EXPIRY] });
    if (url.includes("/api/options/chain")) return json({ symbol: TICKER, expiry: EXPIRY, strikes: STRIKES });
    if (url.includes("/api/risk-free-rate")) return json({ rate: 0 });
    if (url.includes("/api/previous-close")) return json({ closes: { [TICKER]: SPOT } });
    if (url.includes("/api/profile")) return json({});
    return Promise.reject(new Error(`Unexpected fetch: ${url}`));
  }));
}

function renderChain(prices: Record<string, PriceData>) {
  return render(
    <TickerDetailProvider>
      <OptionsChainTab ticker={TICKER} prices={prices} tickerPriceData={prices[TICKER]} />
    </TickerDetailProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  __resetUiPreferencesForTests();
  seedRiskFreeRateForTests(0);
  installFetchMock();
  Object.defineProperty(Element.prototype, "scrollTo", { configurable: true, value: vi.fn() });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("PERF-032 chain implied-value tick budget", () => {
  it("an unrelated symbol tick adds zero computeLegImpliedValue calls", async () => {
    const prices = bookPrices();
    const spy = vi.spyOn(impliedValue, "computeLegImpliedValue");
    const { rerender } = renderChain(prices);
    await waitFor(() => expect(document.querySelectorAll("tr[data-strike]").length).toBe(ROW_COUNT));
    const afterMount = spy.mock.calls.length;
    expect(afterMount).toBeGreaterThan(0);

    rerender(
      <TickerDetailProvider>
        <OptionsChainTab
          ticker={TICKER}
          prices={{ ...prices, SPY: pd({ symbol: "SPY", last: 500 }) }}
          tickerPriceData={prices[TICKER]}
        />
      </TickerDetailProvider>,
    );
    expect(spy.mock.calls.length - afterMount).toBe(0);
    spy.mockRestore();
  });

  it("a tick for one listed contract recomputes only that leg", async () => {
    const prices = bookPrices();
    const spy = vi.spyOn(impliedValue, "computeLegImpliedValue");
    const { rerender } = renderChain(prices);
    await waitFor(() => expect(document.querySelectorAll("tr[data-strike]").length).toBe(ROW_COUNT));
    const afterMount = spy.mock.calls.length;

    const tickedKey = optionKey({ symbol: TICKER, expiry: EXPIRY, strike: 355, right: "C" });
    rerender(
      <TickerDetailProvider>
        <OptionsChainTab
          ticker={TICKER}
          prices={{ ...prices, [tickedKey]: optionPd("C", 355) }}
          tickerPriceData={prices[TICKER]}
        />
      </TickerDetailProvider>,
    );
    const extra = spy.mock.calls.slice(afterMount);
    expect(extra).toHaveLength(1);
    expect(extra[0][0]).toMatchObject({ ticker: TICKER, strike: 355, type: "Call" });
    spy.mockRestore();
  });
});
