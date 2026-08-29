// @vitest-environment jsdom
//
// REL-131 (R-378, R-413): a deep link either arms the contract it NAMES or arms
// nothing. Two failure modes are pinned here:
//   R-378 — the requested expiry is absent from IB's expirations payload, the
//   initial-focus effect falls back to a nearer expiry, and the legs effect then
//   applies the prefill against that fallback because the expiry-mismatch early
//   return never burned `appliedLegsParamRef`.
//   R-413 — a strike the fetched chain does not list produces a phantom leg row.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import TickerDetailContent from "../components/TickerDetailContent";
import { TickerDetailProvider } from "../lib/TickerDetailContext";
import { OrderActionsProvider } from "../lib/OrderActionsContext";
import type { OrdersData, PortfolioData } from "../lib/types";
import type { PriceData } from "../lib/pricesProtocol";

let searchParamsString = "";
const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(searchParamsString),
  usePathname: () => "/MU",
  useRouter: () => ({
    replace: replaceMock,
    push: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn() }),
}));
vi.mock("../components/PriceChart", () => ({
  default: () => React.createElement("div", { "data-testid": "price-chart" }),
}));
vi.mock("../components/QuoteTelemetry", () => ({
  TickerQuoteTelemetry: () => React.createElement("div", { "data-testid": "quote-telemetry" }),
  OrderQuoteTelemetry: () => React.createElement("div", { "data-testid": "order-quote-telemetry" }),
}));

const MU_PRICE: PriceData = {
  symbol: "MU", last: 967.78, lastIsCalculated: false, bid: 967.5, ask: 968.0,
  bidSize: 100, askSize: 100, volume: 1000, high: null, low: null, open: null,
  close: 960, week52High: null, week52Low: null, avgVolume: null, delta: null,
  gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
  timestamp: new Date().toISOString(),
};
const PORTFOLIO: PortfolioData = {
  bankroll: 100_000, peak_value: 100_000, last_sync: new Date().toISOString(),
  total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
  position_count: 0, defined_risk_count: 0, undefined_risk_count: 0,
  avg_kelly_optimal: null, positions: [],
};
const ORDERS: OrdersData = {
  last_sync: new Date().toISOString(), open_orders: [], executed_orders: [],
  open_count: 0, executed_count: 0,
};

const fetchMock = vi.fn<typeof fetch>();

function futureExpiry(days: number) {
  const date = new Date();
  date.setUTCHours(0, 0, 0, 0);
  date.setUTCDate(date.getUTCDate() + days);
  const dashed = date.toISOString().slice(0, 10);
  return { dashed, compact: dashed.replaceAll("-", "") };
}

// The chain lists only these two; the LEAP link below names a third.
const NEAR_EXPIRY = futureExpiry(14);
const FAR_EXPIRY = futureExpiry(42);
const UNLISTED_LEAP_EXPIRY = futureExpiry(500);

function jsonResponse(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }),
  );
}

function renderChain() {
  return render(
    React.createElement(
      OrderActionsProvider,
      null,
      React.createElement(
        TickerDetailProvider,
        null,
        React.createElement(TickerDetailContent, {
          ticker: "MU",
          activeTab: "c",
          onTabChange: vi.fn(),
          prices: { MU: MU_PRICE },
          fundamentals: {},
          portfolio: PORTFOLIO,
          orders: ORDERS,
          theme: "dark",
        }),
      ),
    ),
  );
}

describe("Options chain prefill expiry contract", () => {
  beforeEach(() => {
    searchParamsString = "";
    replaceMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(Element.prototype, "scrollTo", { configurable: true, value: vi.fn() });
    if (!("scrollIntoView" in HTMLElement.prototype)) {
      Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });
    } else {
      vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(() => {});
    }
    fetchMock.mockImplementation((input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : String(input.url);
      if (url.includes("/api/options/expirations")) {
        return jsonResponse({ symbol: "MU", expirations: [NEAR_EXPIRY.compact, FAR_EXPIRY.compact] });
      }
      if (url.includes("/api/options/chain")) {
        return jsonResponse({ symbol: "MU", expiry: NEAR_EXPIRY.compact, strikes: [950, 960, 970] });
      }
      if (url.includes("/api/risk-free-rate")) return jsonResponse({ rate: 0 });
      if (url.includes("/api/ticker/info")) return jsonResponse({ stock_state: {}, uw_info: {}, profile: {}, stats: {} });
      throw new Error(`Unexpected fetch: ${url}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("arms nothing when the requested LEAP expiry is not listed", async () => {
    searchParamsString =
      `deck=c&expiry=${UNLISTED_LEAP_EXPIRY.dashed}&strikes=100&legs=BUY:1x970C&src=leap`;
    renderChain();

    // The fallback expiry resolves and the URL writer runs.
    const expirySelect = (await screen.findAllByRole("combobox"))[0] as HTMLSelectElement;
    await waitFor(() => expect(expirySelect.value).toBe(NEAR_EXPIRY.compact));
    await waitFor(() => expect(replaceMock).toHaveBeenCalled());

    // No prefill may be applied against an expiry the link did not name.
    expect(screen.queryByText("PREFILLED FROM LEAP SCAN")).toBeNull();
    expect(document.querySelector(".order-builder")).toBeNull();

    // And the un-honoured contract must not survive in the URL to be re-applied.
    const url = replaceMock.mock.calls.at(-1)![0] as string;
    expect(url).not.toContain("legs=");
    expect(url).not.toContain("src=");

    // The operator is told, rather than shown a silently different ticket.
    expect((await screen.findByTestId("prefill-unavailable")).textContent).toContain(
      "PREFILL CONTRACT UNAVAILABLE",
    );
  });

  it("keeps the prefill when the requested expiry IS listed", async () => {
    searchParamsString = `deck=c&expiry=${NEAR_EXPIRY.dashed}&strikes=100&legs=BUY:1x970C&src=leap`;
    renderChain();

    await screen.findByText("PREFILLED FROM LEAP SCAN");
    const builder = document.querySelector(".order-builder");
    expect(builder).not.toBeNull();
    expect(builder!.textContent).toContain("1x $970 Call");
    expect(screen.queryByTestId("prefill-unavailable")).toBeNull();
  });

  it("drops a leg whose strike the loaded chain does not list", async () => {
    searchParamsString = `deck=c&expiry=${NEAR_EXPIRY.dashed}&strikes=100&legs=BUY:1x517.5C`;
    renderChain();

    await screen.findByTestId("prefill-unavailable");
    expect(document.querySelector(".order-builder")).toBeNull();
    expect(screen.queryByText(/^PREFILLED FROM/)).toBeNull();
  });
});
