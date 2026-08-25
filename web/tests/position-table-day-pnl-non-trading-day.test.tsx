/**
 * @vitest-environment jsdom
 *
 * IB keeps streaming reqPnLSingle().dailyPnL on weekends and re-baselines it
 * at its daily account rollover, so a Saturday sync carries a five-figure
 * per-position "day" P&L with no session behind it (2026-08-22: +$13,951.76).
 * The Day P&L card is gated on the US trading calendar; the per-position
 * Today P&L column must honour the same gate, with the spot-crypto carve-out
 * (crypto trades through the weekend).
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import PositionTable from "../components/PositionTable";
import type { PortfolioPosition } from "../lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

const IB_WEEKEND_DAILY_PNL = 13_951.76;

const EQUITY_OPTION: PortfolioPosition = {
  id: "AAPL-call",
  ticker: "AAPL",
  structure: "Long Call $200",
  structure_type: "Long Call",
  risk_profile: "defined",
  expiry: "2026-12-18",
  contracts: 10,
  direction: "LONG",
  entry_cost: 12_000,
  max_risk: 12_000,
  market_value: 25_000,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-06-01",
  ib_daily_pnl: IB_WEEKEND_DAILY_PNL,
  legs: [
    {
      direction: "LONG",
      contracts: 10,
      type: "Call",
      strike: 200,
      entry_cost: 12_000,
      avg_cost: 12,
      market_price: 25,
      market_value: 25_000,
    },
  ],
} as unknown as PortfolioPosition;

const CRYPTO: PortfolioPosition = {
  id: "BTC-crypto",
  ticker: "BTC",
  structure: "Crypto",
  structure_type: "Crypto",
  risk_profile: "defined",
  expiry: null,
  contracts: 2,
  direction: "LONG",
  entry_cost: 120_000,
  max_risk: 120_000,
  market_value: 130_000,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-04-01",
  ib_daily_pnl: 1_000,
  legs: [],
} as unknown as PortfolioPosition;

function showAllColumns() {
  window.localStorage.setItem(
    "radon:columns:positions",
    JSON.stringify({
      qty: true, avg_entry: true, last_price: true, implied: true,
      implied_market_value: true, daily_chg: true, today_pnl: true,
      entry_cost: true, market_value: true,
    }),
  );
}

function rowTextAt(instant: string, positions: PortfolioPosition[], ticker: string): string {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(instant));
  showAllColumns();
  render(<PositionTable positions={positions} showUnderlying />);
  const row = screen.getByText(ticker).closest("tr");
  expect(row).not.toBeNull();
  return row?.textContent ?? "";
}

const SATURDAY = "2026-08-22T21:23:00Z"; // Sat 17:23 ET
const FRIDAY_RTH = "2026-08-21T18:30:00Z"; // Fri 14:30 ET

describe("PositionTable Today P&L on a non-trading day", () => {
  it("blanks an equity option's IB daily P&L on Saturday", () => {
    const text = rowTextAt(SATURDAY, [EQUITY_OPTION], "AAPL");
    expect(text).not.toContain("13,952");
    expect(text).not.toContain("13,951");
  });

  it("still shows the IB daily P&L during a weekday session", () => {
    const text = rowTextAt(FRIDAY_RTH, [EQUITY_OPTION], "AAPL");
    expect(text).toContain("+$13,952");
  });

  it("keeps spot crypto's IB daily P&L on Saturday", () => {
    const text = rowTextAt(SATURDAY, [CRYPTO], "BTC");
    expect(text).toContain("+$1,000");
  });
});
