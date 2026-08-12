// @vitest-environment jsdom
//
// Changing the chain EXPIRY dropdown must NOT wipe the order builder. Legs
// carry their own expiry all the way into the combo payload, so browsing to a
// different expiry (or building a calendar) has to keep what is already there.
// Only the explicit CLEAR control empties the builder.

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

function findStrikeRow(strike: number) {
  const strikeCell = Array.from(document.querySelectorAll("td.chain-strike")).find(
    (cell) => Number((cell.textContent ?? "").replace(/[^0-9.]/g, "")) === strike,
  );
  if (!strikeCell) throw new Error(`No chain row for strike ${strike}`);
  return strikeCell.closest("tr")!;
}

function clickCallAsk(strike: number) {
  const row = findStrikeRow(strike);
  // Call side renders first: bid (SELL), mid (BUY), ask (BUY).
  const callAsk = row.querySelectorAll("td.chain-clickable")[2];
  fireEvent.click(callAsk);
}

describe("Options chain expiry change", () => {
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
        return jsonResponse({ symbol: "MU", expirations: [NEAR_EXPIRY.compact, FAR_EXPIRY.compact] });
      }
      if (url.includes("/api/options/chain")) {
        const expiry = url.includes(FAR_EXPIRY.compact) ? FAR_EXPIRY.compact : NEAR_EXPIRY.compact;
        return jsonResponse({ symbol: "MU", expiry, strikes: [950, 960, 970] });
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

  it("keeps built legs when the expiry dropdown changes", async () => {
    renderChain();

    const expirySelect = (await screen.findAllByRole("combobox"))[0] as HTMLSelectElement;
    await waitFor(() => expect(expirySelect.value).toBe(NEAR_EXPIRY.compact));
    await waitFor(() => findStrikeRow(970));

    clickCallAsk(970);

    const builder = await waitFor(() => {
      const el = document.querySelector(".order-builder");
      if (!el) throw new Error("order builder not rendered");
      return el as HTMLElement;
    });
    expect(builder.textContent).toContain("1x $970 Call");

    fireEvent.change(expirySelect, { target: { value: FAR_EXPIRY.compact } });
    await waitFor(() => expect(expirySelect.value).toBe(FAR_EXPIRY.compact));

    const stillThere = document.querySelector(".order-builder") as HTMLElement | null;
    expect(stillThere).not.toBeNull();
    expect(stillThere!.textContent).toContain("1x $970 Call");
    // The leg keeps the expiry it was built on, not the newly selected one.
    expect(stillThere!.textContent).toContain(NEAR_EXPIRY.dashed);
    expect(within(stillThere!).getAllByTestId("order-builder-leg").length).toBe(1);
  });

  it("clears the builder only on the explicit CLEAR control", async () => {
    renderChain();

    const expirySelect = (await screen.findAllByRole("combobox"))[0] as HTMLSelectElement;
    await waitFor(() => expect(expirySelect.value).toBe(NEAR_EXPIRY.compact));
    await waitFor(() => findStrikeRow(970));

    clickCallAsk(970);
    const builder = await waitFor(() => {
      const el = document.querySelector(".order-builder");
      if (!el) throw new Error("order builder not rendered");
      return el as HTMLElement;
    });

    fireEvent.click(within(builder).getByRole("button", { name: /CLEAR/i }));
    await waitFor(() => expect(document.querySelector(".order-builder")).toBeNull());
  });
});
