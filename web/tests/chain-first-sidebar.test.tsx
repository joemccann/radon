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
  it("shows a failed watchlist mutation only in the toast viewport", async () => {
    watchlist.toggleWatch.mockRejectedValueOnce(new Error("TypeError: internal failure"));
    const { container } = sidebar();
    const star = screen.getByTestId("star-toggle") as HTMLButtonElement;
    await act(async () => { fireEvent.click(star); });
    expect(watchlist.toggleWatch).toHaveBeenCalledExactlyOnceWith("NVDA");
    expect(star.disabled).toBe(false);
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(document.querySelector('#radon-toast-viewport [role="alert"]')?.textContent).toContain("watchlist could not be updated");
  });

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
      disclosure.open = true;
      fireEvent.click(within(disclosure).getByRole("button", { name, exact: true }));
      expect(onDeckChange).toHaveBeenLastCalledWith(key);
    }
  });

  it.each([
    ["c", /^Options chain/], ["p", /^Position/], ["n", /^News/],
  ] as const)("marks only the selected primary view for deck %s", (activeDeck, name) => {
    sidebar({ activeDeck });
    const nav = screen.getByRole("navigation", { name: "Instrument views" });
    expect(within(nav).getByRole("button", { name }).getAttribute("aria-current")).toBe("page");
    expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
    expect(nav.querySelector("summary")?.textContent).toContain("More views");
  });

  it.each([
    ["r", "Ratings"], ["s", "Seasonality"], ["i", "Company"],
    ["h", "13F holdings"], ["f", "Filings"], [":", "Commands"],
  ] as const)("reflects the selected secondary view %s in the disclosure", (activeDeck, label) => {
    sidebar({ activeDeck });
    const nav = screen.getByRole("navigation", { name: "Instrument views" });
    const details = nav.querySelector("details")!;
    const summary = details.querySelector("summary")!;
    expect(summary.textContent).toContain(label);
    expect(summary.getAttribute("data-active")).toBe("true");
    details.open = true;
    expect(within(details).getByRole("button", { name: label, exact: true }).getAttribute("aria-current")).toBe("page");
    expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
  });

  it("marks Book & trade when no deck is selected", () => {
    sidebar({ activeDeck: null });
    const bookButton = screen.getByTestId("chain-instrument-sidebar").querySelector(":scope > button");
    expect(bookButton?.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("button", { name: /^Options chain/ }).hasAttribute("aria-current")).toBe(false);
  });

  it("closes More views on Escape before the workspace can close its deck", () => {
    const { onDeckChange } = sidebar({ activeDeck: "i" });
    const details = screen.getByRole("navigation", { name: "Instrument views" }).querySelector("details")!;
    const summary = details.querySelector("summary")!;
    details.open = true;
    const workspaceEscape = vi.fn();
    document.addEventListener("keydown", workspaceEscape);
    try {
      fireEvent.keyDown(summary, { key: "Escape" });
      expect(details.open).toBe(false);
      expect(document.activeElement).toBe(summary);
      expect(workspaceEscape).not.toHaveBeenCalled();
      expect(onDeckChange).not.toHaveBeenCalled();
      // Once closed, Escape belongs to the instrument workspace again.
      fireEvent.keyDown(summary, { key: "Escape" });
      expect(workspaceEscape).toHaveBeenCalledOnce();
    } finally {
      document.removeEventListener("keydown", workspaceEscape);
    }
  });

  it("closes the native disclosure and preserves focus when choosing a secondary view", () => {
    const { onDeckChange } = sidebar();
    const details = screen.getByRole("navigation", { name: "Instrument views" }).querySelector("details")!;
    details.open = true;
    fireEvent.click(within(details).getByRole("button", { name: "Company", exact: true }));
    expect(onDeckChange).toHaveBeenLastCalledWith("i");
    expect(details.open).toBe(false);
    expect(document.activeElement).toBe(details.querySelector("summary"));
  });

  it("preserves the watchlist control and releases its busy state", async () => {
    sidebar();
    const star = screen.getByTestId("star-toggle") as HTMLButtonElement;
    await act(async () => { fireEvent.click(star); });
    expect(watchlist.toggleWatch).toHaveBeenCalledExactlyOnceWith("NVDA");
    expect(star.disabled).toBe(false);
  });
});
