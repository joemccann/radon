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

// The navigation mock is STATEFUL (T-321). A static `useSearchParams` that
// returns the ORIGINAL query on every render can never observe what
// `router.replace` wrote, so the R-378 strip in `useChainUrlState.syncUrl` was
// only ever asserted against whichever `replace` call happened to be last. This
// store mirrors the App Router contract that the hook relies on: one stable
// `URLSearchParams` instance per committed URL, a stable router object, and a
// re-render of every `useSearchParams` consumer when `replace` commits.
const nav = vi.hoisted(() => {
  const listeners = new Set<() => void>();
  let query = "";
  let snapshot = new URLSearchParams(query);
  const commit = (next: string) => {
    query = next;
    snapshot = new URLSearchParams(next);
  };
  return {
    get query() {
      return query;
    },
    snapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    reset: (next: string) => commit(next),
    navigate: (url: string) => {
      commit(url.split("?")[1] ?? "");
      listeners.forEach((listener) => listener());
    },
  };
});
const replaceMock = vi.hoisted(() => vi.fn<(url: string, options?: { scroll?: boolean }) => void>());
vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  const router = {
    replace: (url: string, options?: { scroll?: boolean }) => {
      replaceMock(url, options);
      nav.navigate(url);
    },
    push: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  };
  return {
    useSearchParams: () => useSyncExternalStore(nav.subscribe, nav.snapshot, nav.snapshot),
    usePathname: () => "/MU",
    useRouter: () => router,
  };
});

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
    nav.reset("");
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
    nav.reset(`deck=c&expiry=${UNLISTED_LEAP_EXPIRY.dashed}&strikes=100&legs=BUY:1x970C&src=leap`);
    renderChain();

    // The fallback expiry resolves and the URL writer rewrites ?expiry to it.
    const expirySelect = (await screen.findAllByRole("combobox"))[0] as HTMLSelectElement;
    await waitFor(() => expect(expirySelect.value).toBe(NEAR_EXPIRY.compact));

    // The un-honoured contract must not survive in the COMMITTED URL, where the
    // next render reads it back and would re-apply it against the fallback.
    await waitFor(() => {
      expect(nav.query).toContain(`expiry=${NEAR_EXPIRY.dashed}`);
      expect(nav.query).not.toContain("legs=");
      expect(nav.query).not.toContain("src=");
    });
    expect(nav.query).toContain("deck=c");

    // Every write carried the resolved expiry: a write that dropped ?expiry while
    // keeping ?legs/?src would leave the next pass with no requested expiry to
    // mismatch against, and the prefill would arm under the scanner label.
    expect(replaceMock).toHaveBeenCalled();
    for (const [url] of replaceMock.mock.calls) {
      expect(url).toContain(`expiry=${NEAR_EXPIRY.dashed}`);
    }

    // The operator is told, rather than shown a silently different ticket.
    expect((await screen.findByTestId("prefill-unavailable")).textContent).toContain(
      "PREFILL CONTRACT UNAVAILABLE",
    );

    // No prefill may be applied against an expiry the link did not name, on
    // any render — including the one that read the rewritten URL back.
    await waitFor(() => expect(screen.queryByTestId("prefill-unavailable")).not.toBeNull());
    expect(screen.queryByText("PREFILLED FROM LEAP SCAN")).toBeNull();
    expect(document.querySelector(".order-builder")).toBeNull();
  });

  it("keeps the prefill when the requested expiry IS listed", async () => {
    nav.reset(`deck=c&expiry=${NEAR_EXPIRY.dashed}&strikes=100&legs=BUY:1x970C&src=leap`);
    renderChain();

    await screen.findByText("PREFILLED FROM LEAP SCAN");
    const builder = document.querySelector(".order-builder");
    expect(builder).not.toBeNull();
    expect(builder!.textContent).toContain("1x $970 Call");
    expect(screen.queryByTestId("prefill-unavailable")).toBeNull();
  });

  it("drops a leg whose strike the loaded chain does not list", async () => {
    nav.reset(`deck=c&expiry=${NEAR_EXPIRY.dashed}&strikes=100&legs=BUY:1x517.5C`);
    renderChain();

    await screen.findByTestId("prefill-unavailable");
    expect(document.querySelector(".order-builder")).toBeNull();
    expect(screen.queryByText(/^PREFILLED FROM/)).toBeNull();
  });
});
