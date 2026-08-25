// @vitest-environment jsdom
//
// The mobile ladder's detail sheet is the per-contract order-entry step (its
// footer places the BUY / SELL leg), so it must show the SAME nine-field quote
// telemetry the portfolio position drawer gives the operator, not a hand-rolled
// Last / Bid-Ask / Volume subset.

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import MobileChainLadder from "../components/mobile/MobileChainLadder";
import { optionKey } from "../lib/pricesProtocol";
import type { PriceData } from "../lib/pricesProtocol";

const EXPIRY = "20260821";
const TICKER = "MU";
const STRIKE = 970;

const CALL_KEY = optionKey({ symbol: TICKER, expiry: EXPIRY, strike: STRIKE, right: "C" });

function strikeRow(strike: number) {
  return {
    strike,
    callKey: optionKey({ symbol: TICKER, expiry: EXPIRY, strike, right: "C" }),
    putKey: optionKey({ symbol: TICKER, expiry: EXPIRY, strike, right: "P" }),
  };
}

function callQuote(): PriceData {
  return {
    symbol: CALL_KEY,
    last: 12.5,
    lastIsCalculated: false,
    bid: 12.3,
    ask: 12.7,
    bidSize: 14,
    askSize: 9,
    volume: 4210,
    high: 13.4,
    low: 11.2,
    open: 11.9,
    close: 11.5,
    week52High: null,
    week52Low: null,
    avgVolume: 12_345,
    delta: 0.55,
    gamma: 0.012,
    theta: -0.44,
    vega: 0.31,
    impliedVol: 0.42,
    undPrice: 967.78,
    timestamp: new Date().toISOString(),
  };
}

function renderLadder(prices: Record<string, PriceData>) {
  Object.defineProperty(Element.prototype, "scrollTo", { configurable: true, value: vi.fn() });
  return render(
    React.createElement(MobileChainLadder, {
      ticker: TICKER,
      expirations: [EXPIRY],
      selectedExpiry: EXPIRY,
      onSelectExpiry: vi.fn(),
      visibleStrikes: [strikeRow(960), strikeRow(STRIKE)],
      atmStrike: STRIKE,
      prices,
      currentPrice: 967.78,
      sideFilter: "both" as const,
      onSideFilterChange: vi.fn(),
      strikesPerSide: 15,
      onStrikesPerSideChange: vi.fn(),
      orderLegs: [],
      onAddLeg: vi.fn(),
      portfolio: null,
    }),
  );
}

function openDetailSheet() {
  fireEvent.click(screen.getByTestId(`mobile-chain-call-${STRIKE}`));
  return screen.getByTestId("mobile-chain-detail-sheet");
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Mobile chain detail sheet quote telemetry", () => {
  it("renders the full nine-field telemetry block for the tapped contract", () => {
    renderLadder({ [CALL_KEY]: callQuote() });
    const sheet = openDetailSheet();

    const labels = within(sheet)
      .getAllByText(/^(BID|MID|ASK|SPREAD|LAST|MARK|CLOSE|VOLUME|HIGH|LOW|DAY)$/)
      .map((node) => node.textContent);

    expect(labels).toEqual(["BID", "MID", "ASK", "SPREAD", "LAST", "VOLUME", "HIGH", "LOW", "DAY"]);
  });

  it("labels the panel with the tapped contract and uses the tight density", () => {
    renderLadder({ [CALL_KEY]: callQuote() });
    const sheet = openDetailSheet();

    const panel = sheet.querySelector(".price-bar--tight");
    expect(panel).toBeTruthy();
    expect(within(panel as HTMLElement).getByText(`${TICKER} ${STRIKE} Call`)).toBeTruthy();
  });

  it("keeps the option-specific rows the shared model does not cover", () => {
    renderLadder({ [CALL_KEY]: callQuote() });
    const sheet = openDetailSheet();

    for (const label of ["Bid Size / Ask Size", "IV", "Delta", "Gamma", "Theta", "Vega", "Avg Volume"]) {
      expect(within(sheet).getByText(label)).toBeTruthy();
    }
  });

  it("renders the empty telemetry state when the contract has no quote", () => {
    renderLadder({});
    const sheet = openDetailSheet();

    expect(within(sheet).getByText("No real-time data")).toBeTruthy();
    expect(sheet.querySelectorAll(".price-bar-label").length).toBe(0);
  });
});
