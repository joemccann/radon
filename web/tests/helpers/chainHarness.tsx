// @vitest-environment jsdom
//
// Shared options-chain harness. The chain is the entry point for several
// order-entry surfaces (quote telemetry, the docked ticket rail, the verify
// gate), and each of those needs the same fixture: a live MU chain with two
// clickable call strikes and an empty portfolio.
//
// Callers must install the next/navigation and useWatchlist mocks themselves -
// vi.mock is hoisted per test file and cannot be re-exported from here.

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, vi } from "vitest";

import TickerDetailContent from "../../components/TickerDetailContent";
import { TickerDetailProvider } from "../../lib/TickerDetailContext";
import { OrderActionsProvider } from "../../lib/OrderActionsContext";
import type { OrdersData, PortfolioData } from "../../lib/types";
import type { PriceData } from "../../lib/pricesProtocol";

export function futureExpiry(days: number) {
  const date = new Date();
  date.setUTCHours(0, 0, 0, 0);
  date.setUTCDate(date.getUTCDate() + days);
  const dashed = date.toISOString().slice(0, 10);
  return { dashed, compact: dashed.replaceAll("-", "") };
}

/** Window-relative so the fixture never rots (see feedback_window_relative_test_dates). */
export const EXPIRY = futureExpiry(21);

export function optionQuote(
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
  } as PriceData;
}

export const CALL_970_KEY = `MU_${EXPIRY.compact}_970_C`;
export const CALL_960_KEY = `MU_${EXPIRY.compact}_960_C`;

export const PRICES: Record<string, PriceData> = {
  MU: optionQuote("MU", 967.5, 968.0, { last: 967.78, volume: 1_000, high: 970, low: 960, close: 960 }),
  [CALL_970_KEY]: optionQuote(CALL_970_KEY, 12.1, 12.9),
  [CALL_960_KEY]: optionQuote(CALL_960_KEY, 18.4, 19.2),
};

export const PORTFOLIO: PortfolioData = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [],
} as unknown as PortfolioData;

export const ORDERS: OrdersData = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

/**
 * The chain scrolls the ATM strike into view on mount. jsdom ships neither
 * scrollTo nor scrollIntoView, and the missing method surfaces as an uncaught
 * exception inside a passive effect, which masks the real assertion failure.
 */
export function installChainDomStubs() {
  for (const method of ["scrollTo", "scrollIntoView"] as const) {
    if (typeof (Element.prototype as unknown as Record<string, unknown>)[method] !== "function") {
      Object.defineProperty(Element.prototype, method, { configurable: true, value: () => {} });
    }
  }
}

installChainDomStubs();

function jsonResponse(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }),
  );
}

/**
 * Route the chain's own fetches. Callers install this on their fetch mock so
 * the expiry bar and strike rows actually populate; without it the expiry
 * select stays empty and every row query times out.
 */
export function chainFetch(input: RequestInfo | URL): Promise<Response> {
  const url =
    typeof input === "string" ? input : input instanceof URL ? input.toString() : String((input as Request).url);
  if (url.includes("/api/options/expirations")) {
    return jsonResponse({ symbol: "MU", expirations: [EXPIRY.compact] });
  }
  if (url.includes("/api/options/chain")) {
    return jsonResponse({ symbol: "MU", expiry: EXPIRY.compact, strikes: [950, 960, 970] });
  }
  if (url.includes("/api/risk-free-rate")) return jsonResponse({ rate: 0 });
  if (url.includes("/api/ticker/info")) {
    return jsonResponse({ stock_state: {}, uw_info: {}, profile: {}, stats: {} });
  }
  return jsonResponse({});
}

export function renderChain(prices: Record<string, PriceData> = PRICES) {
  return render(
    <OrderActionsProvider>
      <TickerDetailProvider>
        <TickerDetailContent
          ticker="MU"
          activeTab="c"
          onTabChange={vi.fn()}
          prices={prices}
          fundamentals={{}}
          portfolio={PORTFOLIO}
          orders={ORDERS}
          theme="dark"
        />
      </TickerDetailProvider>
    </OrderActionsProvider>,
  );
}

export function findStrikeRow(strike: number): HTMLTableRowElement {
  const strikeCell = Array.from(document.querySelectorAll("td.chain-strike")).find(
    (cell) => Number((cell.textContent ?? "").replace(/[^0-9.]/g, "")) === strike,
  );
  if (!strikeCell) throw new Error(`No chain row for strike ${strike}`);
  return strikeCell.closest("tr")! as HTMLTableRowElement;
}

/** Call side renders bid (SELL), mid (BUY), ask (BUY). */
export function clickCallCell(strike: number, index: number) {
  fireEvent.click(findStrikeRow(strike).querySelectorAll("td.chain-clickable")[index]);
}

/** Render the chain, wait for the expiry + rows to settle, then apply leg clicks. */
export async function chainWithLegs(clicks: () => void) {
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
