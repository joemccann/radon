// @vitest-environment jsdom

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import TickerDetailContent from "../components/TickerDetailContent";
import { TickerDetailProvider } from "../lib/TickerDetailContext";
import { OrderActionsProvider } from "../lib/OrderActionsContext";
import type { OrdersData, PortfolioData } from "../lib/types";
import type { PriceData } from "../lib/pricesProtocol";

// Mutable navigation mock: searchParamsString drives useSearchParams; replaceMock
// captures URL writes. usePathname is the ticker path (mirrors production).
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

const NEAR_EXPIRY = futureExpiry(14);
const FAR_EXPIRY = futureExpiry(42);

function jsonResponse(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }),
  );
}

// Desktop cockpit: activeTab "chain" → the Chain deck opens with the desktop
// OptionsChainTab (combobox). The cockpit always-mounts OrderTab, which needs
// OrderActionsProvider in the tree.
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
          activeTab: "c", // deck key for Chain (cockpit nav contract; opens the Chain deck)
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

describe("Options chain URL deep-link", () => {
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
      // TickerDetailContent fetches stock-state for the after-hours quote-bar fallback.
      if (url.includes("/api/ticker/info")) return jsonResponse({ stock_state: {}, uw_info: {}, profile: {}, stats: {} });
      throw new Error(`Unexpected fetch: ${url}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("hydrates expiry, side, and strikes from the URL", async () => {
    searchParamsString = `tab=chain&expiry=${NEAR_EXPIRY.dashed}&side=calls&strikes=100`;
    renderChain();

    const comboboxes = await screen.findAllByRole("combobox");
    const expirySelect = comboboxes[0] as HTMLSelectElement;
    const strikesSelect = comboboxes[comboboxes.length - 1] as HTMLSelectElement;

    await waitFor(() => expect(expirySelect.value).toBe(NEAR_EXPIRY.compact));
    expect(strikesSelect.value).toBe("100");
    // CALLS side button is the active one.
    const callsBtn = screen.getByRole("button", { name: "CALLS" });
    expect(callsBtn.className).toContain("active");
  });

  it("falls back to defaults for invalid params", async () => {
    searchParamsString = "tab=chain&expiry=2099-01-01&side=foo&strikes=7";
    renderChain();

    const comboboxes = await screen.findAllByRole("combobox");
    const expirySelect = comboboxes[0] as HTMLSelectElement;
    const strikesSelect = comboboxes[comboboxes.length - 1] as HTMLSelectElement;

    // Invalid expiry -> auto-select first >=7d; invalid strikes -> 15; invalid side -> ALL.
    await waitFor(() => expect(expirySelect.value).toBe(NEAR_EXPIRY.compact));
    expect(strikesSelect.value).toBe("15");
    expect(screen.getByRole("button", { name: "ALL" }).className).toContain("active");
  });

  it("writes side to the URL on change while preserving tab", async () => {
    searchParamsString = "tab=chain";
    renderChain();

    // Wait for expiry to resolve (init effect gates the URL writer).
    const expirySelect = (await screen.findAllByRole("combobox"))[0] as HTMLSelectElement;
    await waitFor(() => expect(expirySelect.value).toBe(NEAR_EXPIRY.compact));

    replaceMock.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "PUTS" }));

    await waitFor(() => expect(replaceMock).toHaveBeenCalled());
    const url = replaceMock.mock.calls.at(-1)![0] as string;
    expect(url).toContain("tab=chain");
    expect(url).toContain("side=puts");
  });

  it("hydrates theta harvester short-strangle legs into the order builder", async () => {
    searchParamsString = `deck=c&expiry=${NEAR_EXPIRY.dashed}&strikes=100&legs=SELL:1x950P,SELL:1x970C`;
    renderChain();

    await screen.findByText("PREFILLED FROM THETA HARVESTER");
    const builder = document.querySelector(".order-builder");
    expect(builder).not.toBeNull();
    expect(builder!.textContent).toContain("ORDER BUILDER : Short Strangle");
    expect(builder!.textContent).toContain("1x $950 Put");
    expect(builder!.textContent).toContain("1x $970 Call");
    expect(builder!.textContent).toContain(NEAR_EXPIRY.dashed);

    const sellButtons = within(builder as HTMLElement).getAllByRole("button", { name: "SELL" });
    expect(sellButtons.length).toBe(2);
  });

  it("labels vol-cone prefills as PREFILLED FROM VOL CONE", async () => {
    searchParamsString = `deck=c&expiry=${NEAR_EXPIRY.dashed}&strikes=100&legs=BUY:1x950P,BUY:1x970C&src=vol-cone`;
    renderChain();

    await screen.findByText("PREFILLED FROM VOL CONE");
    expect(screen.queryByText("PREFILLED FROM THETA HARVESTER")).toBeNull();
    const builder = document.querySelector(".order-builder");
    expect(builder).not.toBeNull();
    expect(builder!.textContent).toContain("ORDER BUILDER : Long Strangle");
    expect(builder!.textContent).toContain("1x $950 Put");
    expect(builder!.textContent).toContain("1x $970 Call");
  });

  it("keeps the theta prefill label when src is absent even for long-vol BUY legs", async () => {
    searchParamsString = `deck=c&expiry=${NEAR_EXPIRY.dashed}&strikes=100&legs=BUY:1x950P,BUY:1x970C`;
    renderChain();

    await screen.findByText("PREFILLED FROM THETA HARVESTER");
    expect(screen.queryByText("PREFILLED FROM VOL CONE")).toBeNull();
  });
});
