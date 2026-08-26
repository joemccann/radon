/**
 * @vitest-environment jsdom
 *
 * A position opened TODAY has no yesterday, so its Today P&L IS its total
 * P&L — the invariant `getTodayPnlDollars === getPnlDollars` for any same-day
 * position, on every surface that renders both numbers.
 *
 * 2026-08-26 broke it on mobile: META 40x short $580 put opened that morning
 * rendered `+$1,097` total and `-$103` today. Both halves were right about
 * their own inputs and wrong about each other — the card resolved its market
 * value from a private copy of the real-time MV walk that reads `prices[k].last`
 * raw, while `getTodayPnlDollars` resolved the same legs through
 * `resolveRealtimePrice`, which swaps a wide-spread `last` for the mid. Two
 * market values for one position, so the identity could not hold.
 *
 * The numbers below are that position, and the wide spread that separates the
 * two resolutions: last 2.73, bid 2.73 / ask 3.33 (mid 3.03, 19.8% spread).
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import PositionTable from "../components/PositionTable";
import MobilePositionList from "../components/mobile/MobilePositionList";
import { getPnlDollars, getTodayPnlDollars, resolveRealtimeMarketValue, resolveMarketValue } from "../lib/positionUtils";
import { optionKey } from "../lib/pricesProtocol";
import type { PriceData } from "../lib/pricesProtocol";
import type { PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

afterEach(cleanup);

function todayET(): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function makePriceData(overrides: Partial<PriceData> = {}): PriceData {
  return {
    symbol: "TEST", last: null, lastIsCalculated: false,
    bid: null, ask: null, bidSize: null, askSize: null,
    volume: null, high: null, low: null, open: null, close: null,
    week52High: null, week52Low: null, avgVolume: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

const EXPIRY = todayET();

const META_SHORT_PUT: PortfolioPosition = {
  id: 42,
  ticker: "META",
  structure: "Short Put $580.0",
  structure_type: "Short Put",
  risk_profile: "undefined",
  expiry: EXPIRY,
  contracts: 40,
  direction: "SHORT",
  entry_cost: -12_017,
  max_risk: null,
  market_value: -10_920,
  ib_daily_pnl: -103,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: todayET(),
  legs: [{
    direction: "SHORT" as const,
    contracts: 40,
    type: "Put" as const,
    strike: 580,
    entry_cost: -12_017,
    avg_cost: 300.425,
    market_price: 2.73,
    market_value: -10_920,
  }],
};

const META_KEY = optionKey({
  symbol: "META",
  expiry: EXPIRY.replace(/-/g, ""),
  strike: 580,
  right: "P",
});

// The spread that splits the two resolutions: raw `last` says 2.73, and
// resolveRealtimePrice returns the 3.03 mid (spread 19.8% of mid, last 9.9%
// away from it).
const WIDE_SPREAD_PRICES: Record<string, PriceData> = {
  [META_KEY]: makePriceData({ symbol: META_KEY, last: 2.73, bid: 2.73, ask: 3.33, close: 3.20 }),
  META: makePriceData({ symbol: "META", last: 577.39, close: 582.4 }),
};

describe("same-day P&L: one market value per position", () => {
  it("resolves one real-time market value, so Today equals Total for a same-day position", () => {
    const mv = resolveRealtimeMarketValue(META_SHORT_PUT, WIDE_SPREAD_PRICES) ?? resolveMarketValue(META_SHORT_PUT);
    expect(getTodayPnlDollars(META_SHORT_PUT, WIDE_SPREAD_PRICES)).toBe(getPnlDollars(META_SHORT_PUT, mv));
  });

  it("ignores a stale ib_daily_pnl on a position that did not exist yesterday", () => {
    // IB reported -$103 for a position opened this morning. Same-day must not
    // read it — that is the number the broken card published.
    expect(getTodayPnlDollars(META_SHORT_PUT, WIDE_SPREAD_PRICES)).not.toBe(META_SHORT_PUT.ib_daily_pnl);
  });

  it("renders the same Today and total P&L on the mobile card", () => {
    render(<MobilePositionList positions={[META_SHORT_PUT]} prices={WIDE_SPREAD_PRICES} />);
    const card = screen.getByTestId("mobile-position-META");
    const todayCell = within(card).getByText("Today").parentElement!;
    const rendered = todayCell.textContent!.replace("Today", "").trim();

    const mv = resolveRealtimeMarketValue(META_SHORT_PUT, WIDE_SPREAD_PRICES) ?? resolveMarketValue(META_SHORT_PUT);
    const total = getPnlDollars(META_SHORT_PUT, mv)!;
    // Both halves read off the same market value, so the card's Today and its
    // headline P&L are the same number.
    const headline = within(card).getByTestId("mobile-position-pnl").textContent!.trim();
    expect(rendered.replace(/^\+/, "")).toBe(headline.replace(/^\+/, ""));
    expect(rendered).toContain(Math.round(Math.abs(total)).toLocaleString());
  });

  it("renders the same Today and total P&L in the desktop table", () => {
    render(<PositionTable positions={[META_SHORT_PUT]} prices={WIDE_SPREAD_PRICES} />);
    const row = screen.getByText("META").closest("tr")!;
    const mv = resolveRealtimeMarketValue(META_SHORT_PUT, WIDE_SPREAD_PRICES) ?? resolveMarketValue(META_SHORT_PUT);
    const total = getPnlDollars(META_SHORT_PUT, mv)!;
    const label = Math.round(Math.abs(total)).toLocaleString();
    expect(within(row).getAllByText(new RegExp(`\\$${label}`)).length).toBeGreaterThanOrEqual(2);
  });

  it("agrees between desktop and mobile on the market value it renders", () => {
    const desktop = render(<PositionTable positions={[META_SHORT_PUT]} prices={WIDE_SPREAD_PRICES} />);
    const desktopRowText = screen.getByText("META").closest("tr")!.textContent ?? "";
    desktop.unmount();

    render(<MobilePositionList positions={[META_SHORT_PUT]} prices={WIDE_SPREAD_PRICES} />);
    const card = screen.getByTestId("mobile-position-META");
    const mobileMv = within(card).getByText("MV").parentElement!.textContent!.replace("MV", "").trim();

    const expected = resolveRealtimeMarketValue(META_SHORT_PUT, WIDE_SPREAD_PRICES)!;
    expect(Math.round(expected)).toBe(-12_120);
    expect(mobileMv).toContain("12,120");
    expect(desktopRowText).toContain("12,120");
  });
});
