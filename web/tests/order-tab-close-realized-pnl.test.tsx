/**
 * @vitest-environment jsdom
 *
 * Regression: when a user opens the Order tab on a held LONG single-leg
 * option and submits a SELL-to-close limit, the confirmation summary
 * must report the cash flow as Proceeds (positive credit, not a
 * negative "Total") and surface the Est. Realized P&L vs. the entry
 * cost basis — NOT the open-position Max Gain / Max Loss (both zero by
 * construction for a covered close, but uninformative to the operator).
 *
 * Originally observed on a LONG 65× USAX Call $45 position where the
 * summary read "Total: -$26,000 · Max Gain: $0 · Max Loss: $0".
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import OrderTab from "../components/ticker-detail/OrderTab";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({
    pendingCancels: new Map(),
    pendingModifies: new Map(),
    cancelledOrders: [],
    requestCancel: vi.fn(),
    requestModify: vi.fn(),
    pushNotification: vi.fn(),
    drainNotifications: vi.fn(() => []),
    setOrdersUpdater: vi.fn(),
  }),
  useOrderActionsOptional: () => ({ pushNotification: vi.fn() }),
}));

vi.mock("@/components/ModifyOrderModal", () => ({
  default: () => null,
}));

/** LONG 65× USAX Call $45.0 at avg_cost $150 per-contract (= $1.50/share).
 *  entry_cost = 65 × 150 = $9,750. Per IB convention, `leg.avg_cost`
 *  for options is per-contract (already × multiplier); same shape that
 *  PositionTable / InstrumentDetail divide by 100 to display per-share. */
const LONG_USAX_CALL: PortfolioPosition = {
  id: 11,
  ticker: "USAX",
  structure: "Long Call $45.0",
  structure_type: "Long Call",
  risk_profile: "defined",
  expiry: "2027-01-15",
  contracts: 65,
  direction: "LONG",
  entry_cost: 9750,
  max_risk: 9750,
  market_value: 26000,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-03-01",
  legs: [
    {
      direction: "LONG",
      contracts: 65,
      type: "Call",
      strike: 45,
      entry_cost: 9750,
      avg_cost: 150,
      market_price: 4.0,
      market_value: 26000,
      market_price_is_calculated: false,
    },
  ],
};

/** Production-repro fixture: LONG 65× USAX Call $45.0, avg_cost $102/contract
 *  (~$1.02/share). User sells at $4.00 limit → expected Realized P&L
 *  = 65 × $4.00 × 100 − 65 × $102 = $26,000 − $6,630 = +$19,370.
 *  Pre-fix the code multiplied the already-per-contract avg_cost by 100
 *  again, giving costBasis = $661,055 → P&L = −$635,055. */
const LONG_USAX_CALL_PROD_REPRO: PortfolioPosition = {
  id: 12,
  ticker: "USAX",
  structure: "Long Call $45.0",
  structure_type: "Long Call",
  risk_profile: "defined",
  expiry: "2027-01-15",
  contracts: 65,
  direction: "LONG",
  entry_cost: 6630,
  max_risk: 6630,
  market_value: 26000,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-03-01",
  legs: [
    {
      direction: "LONG",
      contracts: 65,
      type: "Call",
      strike: 45,
      entry_cost: 6630,
      avg_cost: 102,
      market_price: 4.0,
      market_value: 26000,
      market_price_is_calculated: false,
    },
  ],
};

/** SHORT 10× MU Put $965.0 at avg_cost $5,000 per-contract (= $50.00/share).
 *  Entry CREDIT = 10 × $5,000 = $50,000. Buy-to-close @ $3.00 → close debit
 *  = 10 × $3.00 × 100 = $3,000. Est. Realized P&L = $50,000 − $3,000 = +$47,000.
 *  Screenshot repro: the open-fresh path wrongly showed "Max Gain $962,000 /
 *  Max Loss $3,000" — the risk of OPENING a LONG put, not a buy-to-close. */
const SHORT_MU_PUT: PortfolioPosition = {
  id: 21,
  ticker: "MU",
  structure: "Short Put $965.0",
  structure_type: "Short Put",
  risk_profile: "undefined",
  expiry: "2026-09-18",
  contracts: 10,
  direction: "SHORT",
  entry_cost: -50000,
  max_risk: 965000,
  market_value: -3000,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-03-01",
  legs: [
    {
      direction: "SHORT",
      contracts: 10,
      type: "Put",
      strike: 965,
      entry_cost: -50000,
      avg_cost: 5000,
      market_price: 3.0,
      market_value: -3000,
      market_price_is_calculated: false,
    },
  ],
};

// SHORT 1000 MU shares @ $1,134.97/sh. Covering 500 at $1,030 is a buy-to-close
// that must surface realised P&L (+$52,485), not an open "Total". leg.type Stock
// + strike null → OrderTab's linear/stock path.
const SHORT_MU_STOCK: PortfolioPosition = {
  id: 5,
  ticker: "MU",
  structure: "Stock (-1000.0 shares)",
  structure_type: "Stock",
  risk_profile: "equity",
  expiry: "N/A",
  contracts: 1000,
  direction: "SHORT",
  entry_cost: -1134970,
  max_risk: null,
  market_value: -1060600,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-06-01",
  legs: [
    { direction: "SHORT", contracts: 1000, type: "Stock", strike: null, entry_cost: -1134970, avg_cost: 1134.97, market_price: 1060.6, market_value: -1060600, market_price_is_calculated: false },
  ],
};

const PORTFOLIO: PortfolioData = {
  bankroll: 250_000,
  peak_value: 250_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 4,
  total_deployed_dollars: 9_750,
  remaining_capacity_pct: 96,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [LONG_USAX_CALL],
  account_summary: {
    net_liquidation: 250_000,
    daily_pnl: 0,
    unrealized_pnl: 16_250,
    realized_pnl: 0,
    settled_cash: 240_250,
    maintenance_margin: 0,
    excess_liquidity: 240_250,
    buying_power: 480_500,
    dividends: 0,
  },
};

function makePrice(symbol: string): PriceData {
  return {
    symbol,
    last: 4.0,
    lastIsCalculated: false,
    bid: 3.8,
    ask: 4.1,
    bidSize: 1,
    askSize: 1,
    volume: 100,
    high: null,
    low: null,
    open: null,
    close: 3.9,
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

const PRICES: Record<string, PriceData> = {
  USAX_20270115_45_C: makePrice("USAX_20270115_45_C"),
  USAX: makePrice("USAX"),
};

function makePutPrice(symbol: string): PriceData {
  return { ...makePrice(symbol), last: 3.0, bid: 2.9, ask: 3.1, close: 3.2 };
}

const MU_PRICES: Record<string, PriceData> = {
  MU_20260918_965_P: makePutPrice("MU_20260918_965_P"),
  MU: makePutPrice("MU"),
};

function readSummaryMetrics(container: HTMLElement): Record<string, string> {
  const metrics: Record<string, string> = {};
  container.querySelectorAll(".order-confirm-metric").forEach((row) => {
    const label = row.querySelector(".order-confirm-metric-label")?.textContent?.trim() ?? "";
    const value = row.querySelector(".order-confirm-metric-value")?.textContent?.trim() ?? "";
    if (label) metrics[label.replace(/:$/, "")] = value;
  });
  return metrics;
}

describe("OrderTab — SELL-to-close on LONG single-leg surfaces realized P&L", () => {
  afterEach(cleanup);

  it("reports Proceeds (positive) + Est. Realized P&L instead of Total / Max Gain / Max Loss", () => {
    const { container, getByText } = render(
      React.createElement(OrderTab, {
        ticker: "USAX",
        position: LONG_USAX_CALL,
        portfolio: PORTFOLIO,
        prices: PRICES,
        openOrders: [],
      }),
    );

    // Form defaults action="SELL" because position != null (close path).
    const qtyInput = container.querySelector<HTMLInputElement>(".order-input");
    expect(qtyInput).not.toBeNull();
    fireEvent.change(qtyInput!, { target: { value: "65" } });

    const limitInput = container.querySelector<HTMLInputElement>(".modify-price-input");
    expect(limitInput).not.toBeNull();
    fireEvent.change(limitInput!, { target: { value: "4.00" } });

    // Advance to confirm step so OrderConfirmSummary renders.
    fireEvent.click(getByText("Place Order"));

    const metrics = readSummaryMetrics(container);

    // Bug repro: the summary previously read "Total: -$26,000" + "Max
    // Gain / Max Loss" — both zero. Pin the fix.
    expect(metrics).toHaveProperty("Proceeds");
    expect(metrics.Proceeds).toMatch(/\$26,000/);
    expect(metrics).not.toHaveProperty("Total");

    expect(metrics).toHaveProperty("Est. Realized P&L");
    // Realized P&L = 65 × $4.00 × 100 − 65 × $150 = $26,000 − $9,750 = $16,250.
    expect(metrics["Est. Realized P&L"]).toMatch(/\$16,250/);

    // Max Gain / Max Loss are forward-risk metrics for OPEN trades;
    // they're meaningless on a pure close and must not render.
    expect(metrics).not.toHaveProperty("Max Gain");
    expect(metrics).not.toHaveProperty("Max Loss");
  });

  /**
   * Production repro (2026-05-22): LONG 65× USAX Call $45 at $102/contract
   * avg_cost, SELL @ $4.00. Pre-fix the order summary multiplied the
   * already-per-contract avg_cost by 100 again, returning costBasis
   * $661,055 and Est. Realized P&L −$635,055 instead of +$19,370.
   */
  it("uses per-contract avg_cost directly (no double × multiplier) on close P&L", () => {
    const portfolio: PortfolioData = {
      ...PORTFOLIO,
      positions: [LONG_USAX_CALL_PROD_REPRO],
    };

    const { container, getByText } = render(
      React.createElement(OrderTab, {
        ticker: "USAX",
        position: LONG_USAX_CALL_PROD_REPRO,
        portfolio,
        prices: PRICES,
        openOrders: [],
      }),
    );

    const qtyInput = container.querySelector<HTMLInputElement>(".order-input");
    fireEvent.change(qtyInput!, { target: { value: "65" } });

    const limitInput = container.querySelector<HTMLInputElement>(".modify-price-input");
    fireEvent.change(limitInput!, { target: { value: "4.00" } });

    fireEvent.click(getByText("Place Order"));

    const metrics = readSummaryMetrics(container);

    expect(metrics.Proceeds).toMatch(/\$26,000/);

    // Cost basis = 65 × $102 = $6,630 (per-contract avg_cost, NOT × 100).
    // Realized P&L = $26,000 − $6,630 = +$19,370 profit.
    expect(metrics["Est. Realized P&L"]).toMatch(/\$19,370/);
    // Guardrail: bug surfaced as −$635,055; ensure we're not there.
    expect(metrics["Est. Realized P&L"]).not.toMatch(/635,055/);
  });
});

describe("OrderTab — BUY-to-close on SHORT single-leg surfaces realized P&L", () => {
  afterEach(cleanup);

  it("short-circuits a buy-to-close-short to a close-out (NOT open-fresh long-put risk)", () => {
    const portfolio: PortfolioData = { ...PORTFOLIO, positions: [SHORT_MU_PUT] };

    const { container, getByText } = render(
      React.createElement(OrderTab, {
        ticker: "MU",
        position: SHORT_MU_PUT,
        portfolio,
        prices: MU_PRICES,
        openOrders: [],
      }),
    );

    // Closing a SHORT is a BUY; the form defaults action="SELL", so flip it.
    fireEvent.click(getByText("BUY"));

    const qtyInput = container.querySelector<HTMLInputElement>(".order-input");
    fireEvent.change(qtyInput!, { target: { value: "10" } });

    const limitInput = container.querySelector<HTMLInputElement>(".modify-price-input");
    fireEvent.change(limitInput!, { target: { value: "3.00" } });

    fireEvent.click(getByText("Place Order"));

    const metrics = readSummaryMetrics(container);

    // Screenshot bug repro: open-fresh path rendered "Max Gain $962,000 /
    // Max Loss $3,000". A buy-to-close must surface a close-out instead.
    expect(metrics).not.toHaveProperty("Max Gain");
    expect(metrics).not.toHaveProperty("Max Loss");

    // Close debit = 10 × $3.00 × 100 = $3,000 (positive magnitude on display).
    expect(metrics).toHaveProperty("Close Debit");
    expect(metrics["Close Debit"]).toMatch(/\$3,000/);

    // Est. Realized P&L = entry credit ($50,000) − close debit ($3,000) = +$47,000.
    expect(metrics).toHaveProperty("Est. Realized P&L");
    expect(metrics["Est. Realized P&L"]).toMatch(/\$47,000/);
    // Guardrail: the open-fresh bug surfaced $962,000; ensure we're not there.
    expect(metrics["Est. Realized P&L"]).not.toMatch(/962,000/);
  });
});

describe("OrderTab — buy-to-close-short STOCK surfaces realized P&L", () => {
  afterEach(cleanup);

  it("covering 500 of a 1000-share short at $1,030 shows Est. Realized P&L, not an open Total", () => {
    const { container, getByText } = render(
      React.createElement(OrderTab, {
        ticker: "MU",
        position: SHORT_MU_STOCK,
        portfolio: PORTFOLIO,
        prices: { MU: makePrice("MU") },
        openOrders: [],
      }),
    );

    // Form defaults to SELL on a held position; covering a short is a BUY.
    const actionBtns = Array.from(container.querySelectorAll(".order-action-btn"));
    const buyBtn = actionBtns.find((b) => /buy/i.test(b.textContent ?? ""));
    expect(buyBtn).toBeTruthy();
    fireEvent.click(buyBtn!);

    const qtyInput = container.querySelector<HTMLInputElement>(".order-input");
    fireEvent.change(qtyInput!, { target: { value: "500" } });
    const limitInput = container.querySelector<HTMLInputElement>(".modify-price-input");
    fireEvent.change(limitInput!, { target: { value: "1030" } });

    fireEvent.click(getByText("Place Order"));

    const metrics = readSummaryMetrics(container);
    // basis 500 × $1,134.97 = $567,485; cover 500 × $1,030 = $515,000 → +$52,485.
    expect(metrics).toHaveProperty("Est. Realized P&L");
    expect(metrics["Est. Realized P&L"]).toMatch(/\$52,485/);
    // The bug: showed an open "Total" with no P&L. These must be gone.
    expect(metrics).not.toHaveProperty("Total");
    expect(metrics).not.toHaveProperty("Max Gain");
    expect(metrics).not.toHaveProperty("Max Loss");
  });
});
