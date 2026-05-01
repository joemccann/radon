/**
 * @vitest-environment jsdom
 *
 * Regression: the "Close Position" Bid/Mid/Ask block in the Order tab must
 * agree on sign with the InstrumentDetail header (which uses
 * `resolveSpreadPriceData`). Whatever the header reports for the spread
 * — credit (negative) or debit (positive) — the close-position block is
 * the same combo and must carry the same sign.
 *
 * Reproduces the AMD Reverse Risk Reversal (P$340 / C$350) case where
 * the modal header showed BID -$10.05 / MID -$10.02 / ASK -$10.00 but the
 * close-position block showed BID +$9.30 / MID +$10.02 / ASK +$10.75.
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import OrderTab from "../components/ticker-detail/OrderTab";
import { resolveSpreadPriceData } from "@/lib/positionUtils";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({
    pendingCancels: new Map(),
    pendingModifies: new Map(),
    cancelledOrders: [],
    requestCancel: vi.fn(),
    requestModify: vi.fn(),
    drainNotifications: vi.fn(() => []),
    setOrdersUpdater: vi.fn(),
  }),
}));

vi.mock("@/components/ModifyOrderModal", () => ({
  default: () => null,
}));

const REVERSE_RR_POSITION: PortfolioPosition = {
  id: 99,
  ticker: "AMD",
  structure: "Reverse Risk Reversal (P$340.0/C$350.0)",
  structure_type: "Reverse Risk Reversal",
  risk_profile: "undefined",
  expiry: "2026-05-15",
  contracts: 50,
  direction: "COMBO",
  entry_cost: -35461.86,
  max_risk: null,
  market_value: -48350,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-04-01",
  legs: [
    {
      direction: "SHORT",
      contracts: 50,
      type: "Call",
      strike: 350,
      entry_cost: 111455.7,
      avg_cost: 2229.11,
      market_price: 23.15,
      market_value: 115750,
      market_price_is_calculated: false,
    },
    {
      direction: "LONG",
      contracts: 50,
      type: "Put",
      strike: 340,
      entry_cost: 75993.84,
      avg_cost: 1519.88,
      market_price: 13.48,
      market_value: 67400,
      market_price_is_calculated: false,
    },
  ],
};

const BULL_CALL_DEBIT_POSITION: PortfolioPosition = {
  id: 100,
  ticker: "AMD",
  structure: "Bull Call Spread ($150/$160)",
  structure_type: "Bull Call Spread",
  risk_profile: "defined",
  expiry: "2026-05-15",
  contracts: 10,
  direction: "COMBO",
  entry_cost: 5000,
  max_risk: 5000,
  market_value: 7000,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-04-01",
  legs: [
    {
      direction: "LONG",
      contracts: 10,
      type: "Call",
      strike: 150,
      entry_cost: 12000,
      avg_cost: 1200,
      market_price: 15,
      market_value: 15000,
      market_price_is_calculated: false,
    },
    {
      direction: "SHORT",
      contracts: 10,
      type: "Call",
      strike: 160,
      entry_cost: 7000,
      avg_cost: 700,
      market_price: 8,
      market_value: 8000,
      market_price_is_calculated: false,
    },
  ],
};

const PORTFOLIO: PortfolioData = {
  bankroll: 250_000,
  peak_value: 250_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 1,
  total_deployed_dollars: 1_000,
  remaining_capacity_pct: 99,
  position_count: 1,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  positions: [REVERSE_RR_POSITION],
  account_summary: {
    net_liquidation: 250_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 250_000,
    maintenance_margin: 0,
    excess_liquidity: 250_000,
    buying_power: 500_000,
    dividends: 0,
  },
};

// Reverse RR (LONG put / SHORT call): call > put, so net is a credit (negative).
// put.bid 13.30, put.ask 13.40 / call.bid 23.10, call.ask 23.80
//   netBid (with sign) = put.bid - call.ask = 13.30 - 23.80 = -10.50
//   netAsk (with sign) = put.ask - call.bid = 13.40 - 23.10 = -9.70
//   header bid = min = -10.50, ask = max = -9.70, mid = -10.10  (all negative)
const REVERSE_RR_PRICES: Record<string, PriceData> = {
  AMD_20260515_350_C: makePrice("AMD_20260515_350_C", { bid: 23.1, ask: 23.8, last: 23.15, close: 22.5 }),
  AMD_20260515_340_P: makePrice("AMD_20260515_340_P", { bid: 13.3, ask: 13.4, last: 13.48, close: 13.0 }),
};

// Bull Call Spread (LONG 150 / SHORT 160) is a debit (positive net).
//   long bid 15.00, ask 15.20 / short bid 8.00, ask 8.20
//   netBid = long.bid - short.ask = 15.00 - 8.20 =  6.80
//   netAsk = long.ask - short.bid = 15.20 - 8.00 =  7.20
//   header bid = 6.80, ask = 7.20, mid = 7.00  (all positive)
const BULL_CALL_PRICES: Record<string, PriceData> = {
  AMD_20260515_150_C: makePrice("AMD_20260515_150_C", { bid: 15.0, ask: 15.2, last: 15.1, close: 14.8 }),
  AMD_20260515_160_C: makePrice("AMD_20260515_160_C", { bid: 8.0, ask: 8.2, last: 8.1, close: 7.9 }),
};

function makePrice(symbol: string, fields: { bid: number; ask: number; last: number; close: number }): PriceData {
  return {
    symbol,
    last: fields.last,
    lastIsCalculated: false,
    bid: fields.bid,
    ask: fields.ask,
    bidSize: 1,
    askSize: 1,
    volume: 100,
    high: null,
    low: null,
    open: null,
    close: fields.close,
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
  };
}

function readStripPrices(container: HTMLElement): { bid: number; mid: number; ask: number } {
  const items = container.querySelectorAll(".spread-price-strip .spread-price-item");
  const labelled: Record<string, number> = {};
  items.forEach((el) => {
    const label = el.querySelector(".spread-price-label")?.textContent?.trim();
    const valueText = el.querySelector(".spread-price-value")?.textContent?.trim() ?? "";
    if (!label) return;
    // Format is "-$10.50" or "$7.20" — keep digits, dot and leading minus.
    const numeric = Number(valueText.replace(/[^\d.\-]/g, ""));
    if (label === "BID" || label === "MID" || label === "ASK") {
      labelled[label.toLowerCase()] = numeric;
    }
  });
  return { bid: labelled.bid, mid: labelled.mid, ask: labelled.ask };
}

describe("OrderTab Close Position sign matches header", () => {
  afterEach(cleanup);

  it("Reverse Risk Reversal credit: close-position strip carries the same negative sign as the header", () => {
    // Header source of truth (used by InstrumentDetail / TickerDetailContent).
    const header = resolveSpreadPriceData("AMD", REVERSE_RR_POSITION, REVERSE_RR_PRICES);
    expect(header).not.toBeNull();
    expect(header!.bid! < 0).toBe(true);
    expect(header!.ask! < 0).toBe(true);
    expect(((header!.bid! + header!.ask!) / 2) < 0).toBe(true);

    const { container } = render(
      React.createElement(OrderTab, {
        ticker: "AMD",
        position: REVERSE_RR_POSITION,
        portfolio: PORTFOLIO,
        prices: REVERSE_RR_PRICES,
        openOrders: [],
      }),
    );

    const strip = readStripPrices(container);

    // The bug: strip shows positive when it should be negative.
    // Sign must match the header (which is the source of truth).
    expect(Math.sign(strip.bid)).toBe(Math.sign(header!.bid!));
    expect(Math.sign(strip.mid)).toBe(Math.sign((header!.bid! + header!.ask!) / 2));
    expect(Math.sign(strip.ask)).toBe(Math.sign(header!.ask!));

    // For a credit, all three should be strictly negative.
    expect(strip.bid).toBeLessThan(0);
    expect(strip.mid).toBeLessThan(0);
    expect(strip.ask).toBeLessThan(0);
  });

  it("Bull Call debit spread: close-position strip carries the same positive sign as the header", () => {
    const header = resolveSpreadPriceData("AMD", BULL_CALL_DEBIT_POSITION, BULL_CALL_PRICES);
    expect(header).not.toBeNull();
    expect(header!.bid! > 0).toBe(true);
    expect(header!.ask! > 0).toBe(true);

    const portfolio = { ...PORTFOLIO, positions: [BULL_CALL_DEBIT_POSITION] };
    const { container } = render(
      React.createElement(OrderTab, {
        ticker: "AMD",
        position: BULL_CALL_DEBIT_POSITION,
        portfolio,
        prices: BULL_CALL_PRICES,
        openOrders: [],
      }),
    );

    const strip = readStripPrices(container);
    expect(Math.sign(strip.bid)).toBe(Math.sign(header!.bid!));
    expect(Math.sign(strip.mid)).toBe(Math.sign((header!.bid! + header!.ask!) / 2));
    expect(Math.sign(strip.ask)).toBe(Math.sign(header!.ask!));

    // For a debit, all three should be strictly positive.
    expect(strip.bid).toBeGreaterThan(0);
    expect(strip.mid).toBeGreaterThan(0);
    expect(strip.ask).toBeGreaterThan(0);
  });

  it("strip is invariant to BUY/SELL action toggle (the spread is one structural value)", () => {
    const { container, getByRole } = render(
      React.createElement(OrderTab, {
        ticker: "AMD",
        position: REVERSE_RR_POSITION,
        portfolio: PORTFOLIO,
        prices: REVERSE_RR_PRICES,
        openOrders: [],
      }),
    );

    const initial = readStripPrices(container);

    // Toggle to BUY (buy-back) — the displayed spread value must not change.
    fireEvent.click(getByRole("button", { name: /^BUY$/ }));
    const afterBuy = readStripPrices(container);
    expect(afterBuy.bid).toBeCloseTo(initial.bid, 2);
    expect(afterBuy.mid).toBeCloseTo(initial.mid, 2);
    expect(afterBuy.ask).toBeCloseTo(initial.ask, 2);
  });

  it("strip values exactly match the InstrumentDetail header values for a credit position", () => {
    const header = resolveSpreadPriceData("AMD", REVERSE_RR_POSITION, REVERSE_RR_PRICES);
    expect(header).not.toBeNull();

    const { container } = render(
      React.createElement(OrderTab, {
        ticker: "AMD",
        position: REVERSE_RR_POSITION,
        portfolio: PORTFOLIO,
        prices: REVERSE_RR_PRICES,
        openOrders: [],
      }),
    );
    const strip = readStripPrices(container);
    const headerMid = (header!.bid! + header!.ask!) / 2;

    expect(strip.bid).toBeCloseTo(header!.bid!, 2);
    expect(strip.ask).toBeCloseTo(header!.ask!, 2);
    expect(strip.mid).toBeCloseTo(headerMid, 2);
  });
});
