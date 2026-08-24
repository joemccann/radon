// @vitest-environment jsdom
/**
 * The SUBJECT owns focusedBookKey's lifetime: a leg book pinned on one ticker
 * must be cleared by TickerDetailContent when the ticker changes, not by a
 * shell-side reset inside setActiveTicker (which races child publishes).
 */
import React, { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";

import TickerDetailContent from "../components/TickerDetailContent";
import { TickerDetailProvider, useTickerDetail } from "../lib/TickerDetailContext";
import { OrderActionsProvider } from "../lib/OrderActionsContext";
import type { OrdersData, PortfolioData } from "../lib/types";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/PLTR",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() }),
}));
vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ isLoaded: true, isSignedIn: false, userId: null }),
}));
vi.mock("../components/PriceChart", () => ({
  default: () => React.createElement("div", { "data-testid": "price-chart" }),
}));
vi.mock("../components/QuoteTelemetry", () => ({
  TickerQuoteTelemetry: () => React.createElement("div", { "data-testid": "quote-telemetry" }),
}));

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
        { direction: "LONG", contracts: 20, type: "Call", strike: 155, entry_cost: 5034.01, avg_cost: 251.7, market_price: 2.82, market_value: 5640, market_price_is_calculated: false },
        { direction: "SHORT", contracts: 20, type: "Put", strike: 152.5, entry_cost: 6605.93, avg_cost: 330.29, market_price: 2.98, market_value: 5960, market_price_is_calculated: false },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-24",
    },
  ],
};
const ORDERS: OrdersData = { last_sync: new Date().toISOString(), open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 };

let seenFocusedBookKey: string | null | undefined;
function Probe() {
  seenFocusedBookKey = useTickerDetail().focusedBookKey;
  return null;
}
function PinOnce({ legKey }: { legKey: string }) {
  const { setFocusedBookKey } = useTickerDetail();
  useEffect(() => {
    setFocusedBookKey(legKey);
  }, [legKey, setFocusedBookKey]);
  return null;
}

function Cockpit({ ticker, positionId, pin }: { ticker: string; positionId: number | null; pin: string | null }) {
  return (
    <OrderActionsProvider>
      <TickerDetailProvider>
        {pin && <PinOnce legKey={pin} />}
        <TickerDetailContent
          ticker={ticker}
          positionId={positionId}
          activeTab="book"
          onTabChange={vi.fn()}
          prices={{}}
          fundamentals={{}}
          portfolio={PORTFOLIO}
          orders={ORDERS}
          theme="dark"
        />
        <Probe />
      </TickerDetailProvider>
    </OrderActionsProvider>
  );
}

describe("TickerDetailContent owns focusedBookKey across ticker changes", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }))));
    Object.defineProperty(Element.prototype, "scrollTo", { configurable: true, value: vi.fn() });
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("clears a pinned leg book when the subject switches to another ticker", async () => {
    const legKey = ["PLTR", "20260327", "155", "C"].join("_"); // option price key
    const view = render(<Cockpit ticker="PLTR" positionId={16} pin={legKey} />);
    await waitFor(() => expect(seenFocusedBookKey).toBe(legKey));

    view.rerender(<Cockpit ticker="AAPL" positionId={null} pin={null} />);
    await waitFor(() => expect(seenFocusedBookKey).toBeNull());
  });
});
