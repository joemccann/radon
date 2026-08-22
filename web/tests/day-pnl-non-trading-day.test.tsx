/**
 * @vitest-environment jsdom
 *
 * IB keeps streaming reqPnL().dailyPnL on weekends and re-baselines it at
 * its daily account rollover, so a Saturday sync reported +$13,951.76 with
 * a flat NLV and no session (2026-08-22). The Day P&L card must never call
 * that "TODAY": outside a US trading day the IB aggregate is ignored and the
 * card reads "---" / "MARKET CLOSED".
 */
import React from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

import MetricCards from "../components/MetricCards";

beforeAll(() => {
  if (typeof globalThis.ResizeObserver === "undefined") {
    class Stub {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (globalThis as unknown as { ResizeObserver: typeof Stub }).ResizeObserver = Stub;
  }
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

vi.mock("@/lib/useMarketHours", () => ({
  useMarketHours: () => ({ state: "closed", isOpen: false }),
}));

type Portfolio = Parameters<typeof MetricCards>[0]["portfolio"];

const CRYPTO_POSITION = {
  id: "BTC-crypto",
  ticker: "BTC",
  structure: "Crypto",
  structure_type: "Crypto",
  direction: "LONG",
  qty: 2,
  contracts: 2,
  avg_entry: 60_000,
  cost: 120_000,
  legs: [],
  market_value: 130_000,
  pnl: 10_000,
  pnl_pct: 8.3,
  entry_date: "2026-04-01",
  ib_daily_pnl: null,
};

function buildPortfolio(dailyPnl: number | null, extraPositions: unknown[] = []): Portfolio {
  return {
    bankroll: 1_332_959.5,
    net_leverage: 0.5,
    total_deployed_dollars: 100_000,
    total_pnl_pct: 1.0,
    positions: [
      {
        id: "AAPL-stock",
        ticker: "AAPL",
        structure: "Stock",
        structure_type: "Stock",
        direction: "LONG",
        qty: 100,
        contracts: 100,
        avg_entry: 180,
        cost: 18000,
        legs: [],
        market_value: 19000,
        pnl: 1000,
        pnl_pct: 5.5,
        entry_date: "2026-04-01",
        ib_daily_pnl: null,
      },
      ...extraPositions,
    ],
    account_summary: {
      net_liquidation: 1_332_959.5,
      daily_pnl: dailyPnl,
      unrealized_pnl: -213_745.33,
      realized_pnl: 0,
      buying_power: 3_062_282,
      excess_liquidity: 800_000,
      maint_margin_req: 250_000,
      initial_margin_req: 0,
      settled_cash: 0,
      dividends: 0,
      equity_with_loan: 1_332_959.5,
      previous_day_ewl: 0,
      reg_t_equity: 0,
      sma: 0,
      gross_position_value: 0,
      available_funds: 0,
      cushion: 0.5,
    },
  } as unknown as Portfolio;
}

// Weekend quotes: last == Friday close, so a client-side day move is zero.
const WEEKEND_PRICES = {
  AAPL: { last: 190, close: 190 },
  BTC: { last: 65_500, close: 65_000 }, // crypto trades through the weekend: +$500 × 2
};

function cardText(container: HTMLElement, name: string): string {
  for (const label of Array.from(container.querySelectorAll(".metric-label"))) {
    if ((label.textContent ?? "").trim() === name) {
      return label.parentElement?.textContent ?? "";
    }
  }
  return "";
}

const dayPnlCardText = (c: HTMLElement) => cardText(c, "Day P&L");
const dayMoveCardText = (c: HTMLElement) => cardText(c, "Day Move");

function renderCards(portfolio: Portfolio) {
  return render(
    React.createElement(MetricCards, {
      portfolio,
      prices: WEEKEND_PRICES,
      realizedPnl: 0,
      section: "portfolio",
    } as unknown as Parameters<typeof MetricCards>[0]),
  );
}

describe("Day P&L card on a non-trading day", () => {
  it("ignores IB daily_pnl on Saturday and reads MARKET CLOSED", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-22T21:23:00Z")); // Sat 17:23 ET
    const { container } = renderCards(buildPortfolio(13_951.76));
    const text = dayPnlCardText(container);
    expect(text).toContain("---");
    expect(text).toContain("MARKET CLOSED");
    expect(text).not.toContain("13,951");
    expect(text).not.toContain("TODAY");
    expect(text).not.toContain("ESTIMATED");
  });

  it("ignores IB daily_pnl on a full-closure holiday", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-12-25T18:00:00Z")); // Fri, Christmas
    const { container } = renderCards(buildPortfolio(13_951.76));
    const text = dayPnlCardText(container);
    expect(text).toContain("MARKET CLOSED");
    expect(text).not.toContain("TODAY");
  });

  it("still shows IB daily_pnl as TODAY on a trading day", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-21T18:30:00Z")); // Fri 14:30 ET
    const { container } = renderCards(buildPortfolio(-5_339.04));
    const text = dayPnlCardText(container);
    expect(text).toContain("-$5,339");
    expect(text).toContain("TODAY");
  });

  it("blanks TODAY'S P&L Day Move on Saturday when only equities are held", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-22T21:23:00Z"));
    const { container } = renderCards(buildPortfolio(13_951.76));
    const text = dayMoveCardText(container);
    expect(text).toContain("---");
    expect(text).toContain("MARKET CLOSED");
    expect(text).not.toContain("POSITIONS");
  });

  it("counts only spot crypto toward Day Move on Saturday", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-22T21:23:00Z"));
    const { container } = renderCards(buildPortfolio(null, [CRYPTO_POSITION]));
    const text = dayMoveCardText(container);
    expect(text).toContain("+$1,000");
    expect(text).toContain("1 OF 1 POSITIONS");
  });
});
