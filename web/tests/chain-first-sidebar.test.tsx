// @vitest-environment jsdom

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import ChainInstrumentSidebar from "../components/ticker-detail/ChainInstrumentSidebar";
import { comboQuotePriceData } from "../lib/quoteTelemetry";
import type { PriceData } from "../lib/pricesProtocol";
import type { PortfolioPosition } from "../lib/types";

const watchlist = vi.hoisted(() => ({ isWatched: vi.fn(() => false), toggleWatch: vi.fn(async () => {}) }));
vi.mock("@/lib/useWatchlist", () => ({ useWatchlist: () => watchlist }));

const NOW = new Date("2026-09-16T17:00:00.000Z");
const POSITION: PortfolioPosition = {
  id: 1, ticker: "NVDA", structure: "Call credit spread", structure_type: "Vertical Spread",
  risk_profile: "defined", expiry: "2026-12-18", contracts: 2, direction: "SHORT",
  entry_cost: -700, max_risk: 1300, market_value: -650, legs: [],
  kelly_optimal: null, target: null, stop: null, entry_date: "2026-09-14",
};

function quote(overrides: Partial<PriceData> = {}): PriceData {
  return {
    ...comboQuotePriceData({ symbol: "NVDA", bid: 99.95, ask: 100.05, last: 100, timestamp: NOW.toISOString() }),
    lastIsCalculated: false, close: 99, ...overrides,
  };
}

function sidebar(overrides: Partial<React.ComponentProps<typeof ChainInstrumentSidebar>> = {}) {
  const onDeckChange = vi.fn();
  const result = render(<ChainInstrumentSidebar
    ticker="NVDA"
    position={POSITION}
    underlyingQuote={quote()}
    heldQuote={{ priceData: quote({ symbol: "NVDA_SPREAD", last: -3.25, bid: -3.3, ask: -3.2, close: null, lastIsCalculated: true }), isSpreadNet: true }}
    onDeckChange={onDeckChange}
    {...overrides}
  />);
  return { ...result, onDeckChange };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  watchlist.isWatched.mockReturnValue(false);
  watchlist.toggleWatch.mockClear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("chain-first instrument context", () => {
  it("keeps the underlying last/day separate from the signed held spread mark", () => {
    sidebar();
    const underlying = screen.getByTestId("chain-underlying-quote");
    const held = screen.getByTestId("chain-held-quote");
    expect(underlying.textContent).toContain("Underlying · LAST");
    expect(underlying.textContent).toContain("$100.00");
    expect(underlying.textContent).toContain("+1.01%");
    expect(underlying.textContent).not.toContain("3.25");
    expect(held.textContent).toContain("Spread net");
    expect(held.textContent).toContain("MARK");
    expect(held.textContent).toMatch(/-\$3\.25|\$-3\.25/);
    expect(held.textContent).not.toContain("100.00");
    // A signed spread without a close must never borrow the stock's change.
    expect(held.textContent).toContain("Day---");
  });

  it("retains the resolver's underlying fallback label instead of naming it an option quote", () => {
    sidebar({ heldQuote: { priceData: quote(), label: "NVDA (underlying)", isSpreadNet: false } });
    const held = screen.getByTestId("chain-held-quote");
    expect(held.textContent).toContain("NVDA (underlying)");
    expect(held.textContent).not.toContain("Spread net");
  });

  it("relabels quotes as CLOSE after the stream stops, without requiring a new tick", () => {
    sidebar();
    expect(screen.getByTestId("chain-underlying-quote").textContent).toContain("LAST");
    act(() => { vi.advanceTimersByTime(5 * 60_000 + 30_000); });
    expect(screen.getByTestId("chain-underlying-quote").textContent).toContain("CLOSE");
    expect(screen.getByTestId("chain-held-quote").textContent).toContain("CLOSE");
    expect(screen.getByTestId("chain-underlying-quote").textContent).not.toContain("LAST");
  });

  it("leaves a missing underlying unavailable rather than substituting the held option", () => {
    sidebar({ underlyingQuote: null });
    const underlying = screen.getByTestId("chain-underlying-quote");
    expect(underlying.textContent).toContain("---");
    expect(underlying.textContent).not.toMatch(/3\.25|100\.00/);
    expect(screen.getByTestId("chain-held-quote").textContent).toContain("3.25");
  });

  it("renders a flat position honestly without an empty held-price panel", () => {
    sidebar({ position: null, heldQuote: { priceData: null } });
    expect(screen.getByText("No open position")).toBeTruthy();
    expect(screen.queryByTestId("chain-held-quote")).toBeNull();
    expect(screen.getByRole("link", { name: "Positions" }).getAttribute("href")).toBe("/portfolio");
  });

  it("labels a held equity quantity as shares without an empty expiry separator", () => {
    sidebar({ position: { ...POSITION, structure_type: "Stock", structure: "Long stock", expiry: "", contracts: 100 } });
    expect(screen.getByLabelText("Held position").textContent).toContain("100 shares");
    expect(screen.getByLabelText("Held position").textContent).not.toContain("contracts");
  });

  it("keeps every instrument view reachable through the shared deck callback", () => {
    const { onDeckChange } = sidebar();
    const nav = screen.getByRole("navigation", { name: "Instrument views" });
    for (const [name, key] of [[/^Options chain/, "c"], [/^Position/, "p"], [/^News/, "n"]] as const) {
      fireEvent.click(within(nav).getByRole("button", { name }));
      expect(onDeckChange).toHaveBeenLastCalledWith(key);
    }
    const disclosure = nav.querySelector("details")!;
    // Native disclosure visibility is browser-tested; jsdom does not implement
    // the default summary click action, so expose it explicitly for callbacks.
    disclosure.open = true;
    for (const [name, key] of [["Book & trade", null], ["Ratings", "r"], ["Seasonality", "s"], ["Company", "i"], ["13F holdings", "h"], ["Filings", "f"], ["Commands", ":"]] as const) {
      fireEvent.click(within(disclosure).getByRole("button", { name, exact: true }));
      expect(onDeckChange).toHaveBeenLastCalledWith(key);
    }
  });

  it("preserves the watchlist control and releases its busy state", async () => {
    sidebar();
    const star = screen.getByTestId("star-toggle") as HTMLButtonElement;
    await act(async () => { fireEvent.click(star); });
    expect(watchlist.toggleWatch).toHaveBeenCalledExactlyOnceWith("NVDA");
    expect(star.disabled).toBe(false);
  });
});
