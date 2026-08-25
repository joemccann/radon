/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import PositionTab from "../components/ticker-detail/PositionTab";
import { OrderActionsProvider } from "../lib/OrderActionsContext";
import { TickerDetailProvider, useTickerDetail } from "../lib/TickerDetailContext";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

const POSITION: PortfolioPosition = {
  id: 7, ticker: "VIX", structure: "Combo (3 legs)", structure_type: "Combo",
  direction: "COMBO", contracts: 500, expiry: "2026-06-16", entry_date: "2026-05-27",
  entry_cost: 60438, market_value: 33501,
  legs: [
    { direction: "LONG", type: "Call", strike: 18, contracts: 500, avg_cost: 158, entry_cost: 79000, market_price: 0.99, market_price_is_calculated: false },
    { direction: "SHORT", type: "Call", strike: 28, contracts: 500, avg_cost: 37, entry_cost: -18564, market_price: 0.41, market_price_is_calculated: false },
  ],
} as unknown as PortfolioPosition;

function optPrice(symbol: string): PriceData {
  return {
    symbol, last: 0.9, lastIsCalculated: false, bid: 0.8, ask: 1.0, bidSize: 1, askSize: 1,
    volume: 1, high: null, low: null, open: null, close: null, week52High: null, week52Low: null,
    avgVolume: null, delta: null, gamma: null, theta: null, vega: null, impliedVol: null,
    undPrice: null, timestamp: new Date().toISOString(),
  };
}
const PRICES: Record<string, PriceData> = {
  VIX_20260616_18_C: optPrice("VIX_20260616_18_C"),
  VIX_20260616_28_C: optPrice("VIX_20260616_28_C"),
};
const PORTFOLIO = { positions: [POSITION] } as unknown as PortfolioData;

let captured: ReturnType<typeof useTickerDetail> | null = null;
function Capture() {
  captured = useTickerDetail();
  return null;
}
function renderTab() {
  return render(
    React.createElement(TickerDetailProvider, null,
      React.createElement(OrderActionsProvider, null,
        React.createElement(Capture, null),
        React.createElement(PositionTab, { position: POSITION, prices: PRICES, portfolio: PORTFOLIO }),
      ),
    ),
  );
}

afterEach(() => { cleanup(); captured = null; });

describe("PositionTab — per-leg book focus", () => {
  it("focuses a leg's option book key, then toggles it back off", () => {
    renderTab();
    const btn = screen.getByTestId("pos-leg-book-0");
    expect(btn.textContent).toBe("BOOK");
    fireEvent.click(btn);
    expect(captured!.focusedBookKey).toBe("VIX_20260616_18_C");
    expect(screen.getByTestId("pos-leg-book-0").textContent).toContain("✓");
    // Clicking again clears it (returns the Book pane to the default subject).
    fireEvent.click(screen.getByTestId("pos-leg-book-0"));
    expect(captured!.focusedBookKey).toBeNull();
  });

  it("switching focus between legs replaces the pinned book key", () => {
    renderTab();
    fireEvent.click(screen.getByTestId("pos-leg-book-0"));
    expect(captured!.focusedBookKey).toBe("VIX_20260616_18_C");
    fireEvent.click(screen.getByTestId("pos-leg-book-1"));
    expect(captured!.focusedBookKey).toBe("VIX_20260616_28_C");
  });
});

describe("TickerDetailContext — focusedBookKey lifecycle", () => {
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(TickerDetailProvider, null, children);

  it("does not reset the pinned book key on a ticker change — the subject owns it", () => {
    // TickerDetailContent clears the pin in its ticker-keyed effect cleanup
    // (ticker-detail-content-focused-book-key-clears.test.tsx). A shell-side
    // reset here would race child publishes (child effects run first), which
    // is how client-side navigation lost its depth subscription.
    const { result } = renderHook(() => useTickerDetail(), { wrapper });
    act(() => result.current.setActiveTicker("VIX"));
    act(() => result.current.setFocusedBookKey("VIX_20260616_18_C"));
    expect(result.current.focusedBookKey).toBe("VIX_20260616_18_C");
    act(() => result.current.setActiveTicker("SPX")); // different subject
    expect(result.current.focusedBookKey).toBe("VIX_20260616_18_C");
  });

  it("clears the pinned book key when leaving ticker detail", () => {
    const { result } = renderHook(() => useTickerDetail(), { wrapper });
    act(() => result.current.setActiveTicker("VIX"));
    act(() => result.current.setFocusedBookKey("VIX_20260616_18_C"));
    act(() => result.current.setActiveTicker(null));
    expect(result.current.focusedBookKey).toBeNull();
  });
});
