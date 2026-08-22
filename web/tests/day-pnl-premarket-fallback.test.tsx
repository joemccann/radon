/**
 * @vitest-environment jsdom
 *
 * Regression: the Day P&L card on /portfolio used to render "---" +
 * "MARKET CLOSED" whenever `acct.daily_pnl` was null. IB does not
 * stream `reqPnL().dailyPnL` outside regular trading hours, so the
 * card blanked every pre-market session even though the per-leg
 * day-move math (which the TODAY'S P&L breakdown already used)
 * worked fine from `prices[*].last` and `prices[*].close`.
 *
 * Fix: when `acct.daily_pnl == null` and the client-computed
 * `todayUnrealized` has positions with data, fall back to that
 * aggregate and label the source as "ESTIMATED (PRE-MARKET)".
 */

import React from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

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

afterEach(() => { vi.useRealTimers(); cleanup(); });

vi.mock("@/lib/useMarketHours", () => ({
  useMarketHours: () => ({ state: "closed", isOpen: false }),
}));

type Portfolio = Parameters<typeof MetricCards>[0]["portfolio"];

function buildPortfolio(overrides: {
  daily_pnl: number | null;
  positions?: Portfolio["positions"];
  prices?: Portfolio["prices"];
}): Portfolio {
  const positions = overrides.positions ?? [
    {
      id: "AAPL-stock",
      ticker: "AAPL",
      structure: "Stock",
      // computeDayMoveBreakdown gates on structure_type === "Stock"
      // for the equity branch (uses prices[ticker]).
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
  ];
  return {
    bankroll: 1_500_000,
    net_leverage: 0.5,
    total_deployed_dollars: 100_000,
    total_pnl_pct: 1.0,
    positions,
    account_summary: {
      net_liquidation: 1_534_469.61,
      daily_pnl: overrides.daily_pnl,
      unrealized_pnl: -224_266.34,
      realized_pnl: 3_727,
      buying_power: 4_000_000,
      excess_liquidity: 800_000,
      maint_margin_req: 250_000,
      initial_margin_req: 0,
      settled_cash: 0,
      dividends: 0,
      equity_with_loan: 1_534_469.61,
      previous_day_ewl: 0,
      reg_t_equity: 0,
      sma: 0,
      gross_position_value: 0,
      available_funds: 0,
      cushion: 0.5,
    },
  } as unknown as Portfolio;
}

function buildPrices(): Record<string, { last: number; close: number; mid?: number }> {
  return {
    AAPL: { last: 190, close: 180 }, // +$10/share × 100 shares × LONG = +$1,000 day move
  };
}

/**
 * Find the AccountRow's Day P&L MetricCard text. The lower TodayPnlRow
 * also renders "Day Move" / "MARKET CLOSED" sometimes, so scoped asserts
 * are required to test the top card in isolation.
 */
function dayPnlCardText(container: HTMLElement): string {
  const labels = container.querySelectorAll(".metric-label");
  for (const label of Array.from(labels)) {
    if ((label.textContent ?? "").trim() === "Day P&L") {
      return label.parentElement?.textContent ?? "";
    }
  }
  return "";
}

describe("Day P&L card — pre-market fallback to client-computed aggregate", () => {
  it("renders IB daily_pnl when available (regular hours)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-05T18:30:00Z")); // Fri 14:30 ET
    const portfolio = buildPortfolio({ daily_pnl: -47_215 });
    const { container } = render(
      React.createElement(MetricCards, {
        portfolio,
        prices: buildPrices(),
        realizedPnl: 3_727,
        section: "portfolio",
      } as unknown as Parameters<typeof MetricCards>[0]),
    );

    const cardText = dayPnlCardText(container);
    expect(cardText).toContain("Day P&L");
    expect(cardText).toContain("-$47,215");
    expect(cardText).toContain("TODAY");
    expect(cardText).not.toContain("ESTIMATED");
    expect(cardText).not.toContain("MARKET CLOSED");
    expect(cardText).not.toContain("WAITING FOR IB");
  });

  it('labels the fallback "ESTIMATED (LIVE)" during regular trading hours', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-05T18:30:00Z")); // Fri 14:30 ET
    const portfolio = buildPortfolio({ daily_pnl: null });
    const { container } = render(
      React.createElement(MetricCards, {
        portfolio,
        prices: buildPrices(),
        realizedPnl: 0,
        section: "portfolio",
      } as unknown as Parameters<typeof MetricCards>[0]),
    );

    const cardText = dayPnlCardText(container);
    expect(cardText).toContain("Day P&L");
    // AAPL: 100 long × ($190 - $180) = +$1,000
    expect(cardText).toContain("+$1,000");
    expect(cardText).toContain("ESTIMATED (LIVE)");
    expect(cardText).not.toContain("PRE-MARKET");
    expect(cardText).not.toContain("MARKET CLOSED");
  });

  it('labels the fallback "ESTIMATED (PRE-MARKET)" during the pre-market session', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-05T12:00:00Z")); // Fri 08:00 ET
    const portfolio = buildPortfolio({ daily_pnl: null });
    const { container } = render(
      React.createElement(MetricCards, {
        portfolio,
        prices: buildPrices(),
        realizedPnl: 0,
        section: "portfolio",
      } as unknown as Parameters<typeof MetricCards>[0]),
    );

    const cardText = dayPnlCardText(container);
    expect(cardText).toContain("Day P&L");
    expect(cardText).toContain("+$1,000");
    expect(cardText).toContain("ESTIMATED (PRE-MARKET)");
    fireEvent.click(Array.from(container.querySelectorAll(".metric-label"))
      .find((label) => label.textContent?.trim() === "Day P&L")!.parentElement!);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("+$1,000.00")).toBeTruthy();
    expect(within(dialog).getByText(/Current prices versus prior-session closes/)).toBeTruthy();
  });

  it('labels the fallback "ESTIMATED (AFTER HOURS)" during the after-hours session', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-05T21:00:00Z")); // Fri 17:00 ET
    const portfolio = buildPortfolio({ daily_pnl: null });
    const { container } = render(
      React.createElement(MetricCards, {
        portfolio,
        prices: buildPrices(),
        realizedPnl: 0,
        section: "portfolio",
      } as unknown as Parameters<typeof MetricCards>[0]),
    );

    const cardText = dayPnlCardText(container);
    expect(cardText).toContain("Day P&L");
    expect(cardText).toContain("+$1,000");
    expect(cardText).toContain("ESTIMATED (AFTER HOURS)");
  });

  it('keeps "---" + "MARKET CLOSED" when both IB daily_pnl is null AND no prices/positions feed the fallback', () => {
    const portfolio = buildPortfolio({ daily_pnl: null, positions: [] });
    const { container } = render(
      React.createElement(MetricCards, {
        portfolio,
        prices: {},
        realizedPnl: 0,
        section: "portfolio",
      } as unknown as Parameters<typeof MetricCards>[0]),
    );

    const cardText = dayPnlCardText(container);
    expect(cardText).toContain("Day P&L");
    expect(cardText).toContain("MARKET CLOSED");
    expect(cardText).not.toContain("ESTIMATED");
    expect(cardText).not.toContain("WAITING FOR IB");
  });

  it('shows "WAITING FOR IB" (not "MARKET CLOSED") when positions exist but IB feed is empty', () => {
    // IB Gateway crashed mid-day: daily_pnl is null AND the WS prices
    // map is empty (no live ticks). The honest signal is "infrastructure
    // is down", not "market is closed" — those are very different states
    // and conflating them masks a real outage during trading hours.
    const portfolio = buildPortfolio({ daily_pnl: null });
    const { container } = render(
      React.createElement(MetricCards, {
        portfolio,
        prices: {}, // ← empty, simulates IB Gateway unreachable
        realizedPnl: 0,
        section: "portfolio",
      } as unknown as Parameters<typeof MetricCards>[0]),
    );

    const cardText = dayPnlCardText(container);
    expect(cardText).toContain("Day P&L");
    expect(cardText).toContain("WAITING FOR IB");
    expect(cardText).not.toContain("MARKET CLOSED");
    expect(cardText).not.toContain("ESTIMATED");
  });
});
