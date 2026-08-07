/**
 * @vitest-environment jsdom
 *
 * Mobile position cards, 2026-08-07 report:
 *
 * 1. EC POLARITY. The card rendered `fmtUsd(Math.abs(ec))`, stripping the
 *    credit sign off a net-credit combo — the live SPCX ratio risk reversal
 *    (entry_cost −1,513.60) displayed as a POSITIVE $1,514, reading as a debit
 *    paid when the operator was actually paid to open. This is the EWY
 *    credit-combo bug (web/CLAUDE.md §Sign Convention) repeating on mobile;
 *    the desktop table already routes through `getInitialValue`.
 *
 * 2. UNDERLYING SPOT. An option position showed MV/EC/Today/P&L with no spot,
 *    so there was no way to see where the underlying sat relative to the
 *    strikes without leaving the card.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import MobilePositionList from "../components/mobile/MobilePositionList";
import type { PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(cleanup);

function pd(over: Partial<PriceData> = {}): PriceData {
  return {
    symbol: "X", last: null, lastIsCalculated: false, bid: null, ask: null, bidSize: null,
    askSize: null, volume: null, high: null, low: null, open: null, close: null, week52High: null,
    week52Low: null, avgVolume: null, delta: null, gamma: null, theta: null, vega: null,
    impliedVol: null, undPrice: null, timestamp: new Date().toISOString(), ...over,
  };
}

/** Live 2026-08-07 rows. SPCX opened for a NET CREDIT, META for a net debit. */
const SPCX_CREDIT_COMBO: PortfolioPosition = {
  id: 1, ticker: "SPCX", structure: "Ratio Risk Reversal 60x30 (P$120.0/C$135.0)",
  structure_type: "Ratio Risk Reversal", risk_profile: "undefined", expiry: "2026-08-21",
  contracts: 60, direction: "COMBO", entry_cost: -1513.6, max_risk: null, market_value: 23550,
  kelly_optimal: null, target: null, stop: null, entry_date: "2026-07-28",
  legs: [
    { direction: "LONG", contracts: 60, type: "Call", strike: 135, entry_cost: 48429.07, avg_cost: 807.1511, market_price: 6.55, market_value: 39300 },
    { direction: "SHORT", contracts: 30, type: "Put", strike: 120, entry_cost: 49942.67, avg_cost: 1664.7556, market_price: 5.25, market_value: 15750 },
  ],
};

const META_DEBIT_COMBO: PortfolioPosition = {
  id: 2, ticker: "META", structure: "Ratio Risk Reversal 20x10 (P$560.0/C$620.0)",
  structure_type: "Ratio Risk Reversal", risk_profile: "undefined", expiry: "2026-08-21",
  contracts: 20, direction: "COMBO", entry_cost: 27783.81, max_risk: null, market_value: 10600,
  kelly_optimal: null, target: null, stop: null, entry_date: "2026-07-28",
  legs: [
    { direction: "LONG", contracts: 20, type: "Call", strike: 620, entry_cost: 45168.5, avg_cost: 2258.4248, market_price: 7.45, market_value: 14900 },
    { direction: "SHORT", contracts: 10, type: "Put", strike: 560, entry_cost: 17384.69, avg_cost: 1738.4691, market_price: 4.3, market_value: 4300 },
  ],
};

const SHORT_CALL: PortfolioPosition = {
  id: 3, ticker: "EWY", structure: "Short Call $165", structure_type: "Short Call",
  risk_profile: "undefined", expiry: "2026-09-18", contracts: 25, direction: "SHORT",
  entry_cost: 12518, max_risk: null, market_value: 10000, kelly_optimal: null,
  target: null, stop: null, entry_date: "2026-07-30",
  legs: [{ direction: "SHORT", contracts: 25, type: "Call", strike: 165, entry_cost: 12518, avg_cost: -500.7, market_price: 4.0, market_value: 10000 }],
};

const LONG_STOCK: PortfolioPosition = {
  id: 4, ticker: "MSFT", structure: "Stock (1000.0 shares)", structure_type: "Stock",
  risk_profile: "equity", expiry: "N/A", contracts: 1000, direction: "LONG",
  entry_cost: 463498, max_risk: 463498, market_value: 457690, kelly_optimal: null,
  target: null, stop: null, entry_date: "2026-06-01",
  legs: [{ direction: "LONG", contracts: 1000, type: "Stock", strike: 0, entry_cost: 463498, avg_cost: 463.498, market_price: 457.69, market_value: 457690 }],
};

function ecOf(ticker: string): string {
  const card = screen.getByTestId(`mobile-position-${ticker}`);
  const label = within(card).getByText("EC");
  return label.parentElement?.textContent?.replace("EC", "").trim() ?? "";
}

describe("MobilePositionList — EC polarity", () => {
  it("a net-CREDIT combo shows a NEGATIVE EC (SPCX ratio risk reversal)", () => {
    render(<MobilePositionList positions={[SPCX_CREDIT_COMBO]} prices={{ SPCX: pd({ last: 121.24 }) }} />);
    const ec = ecOf("SPCX");
    expect(ec).toMatch(/^[-−]\$1,514/);
    expect(ec).not.toMatch(/^\$1,514/);
  });

  it("a net-DEBIT combo keeps a POSITIVE EC (META ratio risk reversal)", () => {
    render(<MobilePositionList positions={[META_DEBIT_COMBO]} prices={{ META: pd({ last: 604.2 }) }} />);
    expect(ecOf("META")).toMatch(/^\$27,784/);
  });

  it("a single-leg SHORT option reads its premium credit as negative", () => {
    render(<MobilePositionList positions={[SHORT_CALL]} prices={{ EWY: pd({ last: 157.9 }) }} />);
    expect(ecOf("EWY")).toMatch(/^[-−]\$12,518/);
  });

  it("a long stock keeps a positive notional EC", () => {
    render(<MobilePositionList positions={[LONG_STOCK]} prices={{ MSFT: pd({ last: 457.69 }) }} />);
    expect(ecOf("MSFT")).toMatch(/^\$463,498/);
  });

  it("P&L stays correct while EC flips sign (display change only)", () => {
    render(<MobilePositionList positions={[SPCX_CREDIT_COMBO]} prices={{ SPCX: pd({ last: 121.24 }) }} />);
    // mv 23,550 − ec (−1,513.60) = +25,063.60
    expect(screen.getByTestId("mobile-position-SPCX").textContent ?? "").toMatch(/\+\$25,06\d/);
  });
});

describe("MobilePositionList — underlying spot on option cards", () => {
  it("shows the live underlying price for an option position", () => {
    render(<MobilePositionList positions={[SPCX_CREDIT_COMBO]} prices={{ SPCX: pd({ last: 121.24 }) }} />);
    const spot = screen.getByTestId("mobile-position-SPCX-underlying");
    expect(spot.textContent ?? "").toContain("121.24");
  });

  it("updates with the streamed price, not the stale snapshot", () => {
    const { rerender } = render(
      <MobilePositionList positions={[SPCX_CREDIT_COMBO]} prices={{ SPCX: pd({ last: 121.24 }) }} />,
    );
    rerender(<MobilePositionList positions={[SPCX_CREDIT_COMBO]} prices={{ SPCX: pd({ last: 128.9 }) }} />);
    expect(screen.getByTestId("mobile-position-SPCX-underlying").textContent ?? "").toContain("128.90");
  });

  it("renders no underlying row for a stock position (it IS the underlying)", () => {
    render(<MobilePositionList positions={[LONG_STOCK]} prices={{ MSFT: pd({ last: 457.69 }) }} />);
    expect(screen.queryByTestId("mobile-position-MSFT-underlying")).toBeNull();
  });

  it("omits the underlying row when no price is streaming rather than showing a zero", () => {
    render(<MobilePositionList positions={[SPCX_CREDIT_COMBO]} prices={{}} />);
    expect(screen.queryByTestId("mobile-position-SPCX-underlying")).toBeNull();
  });
});
