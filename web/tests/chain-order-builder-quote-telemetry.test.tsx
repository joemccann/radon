// @vitest-environment jsdom
//
// The options-chain ORDER BUILDER is an order-entry surface, so it must show
// the same nine-field quote telemetry the portfolio position drawer shows:
// BID MID ASK / SPREAD LAST / VOLUME HIGH LOW DAY.
//
// Single leg  -> the contract's own live quote.
// Combo       -> the net combo quote, alongside the interactive OrderPriceStrip
//                (tap-to-fill bid/mid/ask must keep working).

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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

function futureExpiry(days: number) {
  const date = new Date();
  date.setUTCHours(0, 0, 0, 0);
  date.setUTCDate(date.getUTCDate() + days);
  const dashed = date.toISOString().slice(0, 10);
  return { dashed, compact: dashed.replaceAll("-", "") };
}

const EXPIRY = futureExpiry(21);

function optionQuote(
  symbolKey: string,
  bid: number,
  ask: number,
  extra: Partial<PriceData> = {},
): PriceData {
  return {
    symbol: symbolKey,
    last: (bid + ask) / 2,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 10,
    askSize: 10,
    volume: 4321,
    high: ask + 1,
    low: bid - 1,
    open: bid,
    close: bid,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: new Date().toISOString(),
    ...extra,
  };
}

const MU_PRICE: PriceData = optionQuote("MU", 967.5, 968.0, {
  last: 967.78,
  volume: 1_000,
  high: 970,
  low: 960,
  close: 960,
});

const CALL_970_KEY = `MU_${EXPIRY.compact}_970_C`;
const CALL_960_KEY = `MU_${EXPIRY.compact}_960_C`;

const PRICES: Record<string, PriceData> = {
  MU: MU_PRICE,
  [CALL_970_KEY]: optionQuote(CALL_970_KEY, 12.1, 12.9),
  [CALL_960_KEY]: optionQuote(CALL_960_KEY, 18.4, 19.2),
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
          prices: PRICES,
          fundamentals: {},
          portfolio: PORTFOLIO,
          orders: ORDERS,
          theme: "dark",
        }),
      ),
    ),
  );
}

function findStrikeRow(strike: number) {
  const strikeCell = Array.from(document.querySelectorAll("td.chain-strike")).find(
    (cell) => Number((cell.textContent ?? "").replace(/[^0-9.]/g, "")) === strike,
  );
  if (!strikeCell) throw new Error(`No chain row for strike ${strike}`);
  return strikeCell.closest("tr")!;
}

/** Call side renders bid (SELL), mid (BUY), ask (BUY). */
function clickCallCell(strike: number, index: number) {
  fireEvent.click(findStrikeRow(strike).querySelectorAll("td.chain-clickable")[index]);
}

async function builderAfter(clicks: () => void) {
  renderChain();
  const expirySelect = (await screen.findAllByRole("combobox"))[0] as HTMLSelectElement;
  await waitFor(() => expect(expirySelect.value).toBe(EXPIRY.compact));
  await waitFor(() => findStrikeRow(970));
  clicks();
  return waitFor(() => {
    const el = document.querySelector(".order-builder");
    if (!el) throw new Error("order builder not rendered");
    return el as HTMLElement;
  });
}

function telemetryLabels(scope: HTMLElement): string[] {
  return Array.from(scope.querySelectorAll(".price-bar-label")).map(
    (el) => (el.textContent ?? "").trim(),
  );
}

const NINE_FIELDS = ["BID", "MID", "ASK", "SPREAD", "VOLUME", "HIGH", "LOW", "DAY"];

describe("Options chain order builder quote telemetry", () => {
  beforeEach(() => {
    searchParamsString = "tab=chain";
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
        return jsonResponse({ symbol: "MU", expirations: [EXPIRY.compact] });
      }
      if (url.includes("/api/options/chain")) {
        return jsonResponse({ symbol: "MU", expiry: EXPIRY.compact, strikes: [950, 960, 970] });
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

  it("shows the full nine-field telemetry for a single-leg ticket", async () => {
    const builder = await builderAfter(() => clickCallCell(970, 2));

    const panel = builder.querySelector(".order-builder-section--market") as HTMLElement | null;
    expect(panel).not.toBeNull();

    const labels = telemetryLabels(panel!);
    for (const field of NINE_FIELDS) expect(labels).toContain(field);
    expect(labels).toContain("LAST");

    // The contract's own quote, not the underlying's.
    expect(panel!.textContent).toContain("$12.10");
    expect(panel!.textContent).toContain("$12.90");
    expect(panel!.textContent).toContain("4,321");
    expect(panel!.textContent ?? "").not.toMatch(/—/);
  });

  it("shows the net combo telemetry without regressing the tappable price strip", async () => {
    const builder = await builderAfter(() => {
      clickCallCell(970, 2); // BUY 970 call
      clickCallCell(960, 0); // SELL 960 call
    });

    await waitFor(() => expect(within(builder).getAllByTestId("order-builder-leg").length).toBe(2));

    const panel = builder.querySelector(".order-builder-section--market") as HTMLElement;
    const labels = telemetryLabels(panel);
    for (const field of NINE_FIELDS) expect(labels).toContain(field);

    // Tap-to-fill must survive.
    const strip = within(panel).getByTestId("order-price-strip");
    expect(strip).toBeTruthy();
    fireEvent.click(within(panel).getByTestId("order-price-select-bid"));
    const limitInput = builder.querySelector(".modify-price-input") as HTMLInputElement;
    await waitFor(() => expect(limitInput.value).not.toBe(""));
  });
});
