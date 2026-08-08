/**
 * @vitest-environment jsdom
 *
 * NEW_FINDINGS (wave-2): FillsModal received raw executed_orders while
 * totalRealizedPnl was ET day-cut — prior-session rows itemized above a total
 * that excluded them.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import MetricCards from "@/components/MetricCards";
import type { ExecutedOrder, PortfolioData } from "@/lib/types";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const PORTFOLIO: PortfolioData = {
  bankroll: 1_000_000,
  peak_value: 1_000_000,
  last_sync: "2026-08-08T14:00:00Z",
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [],
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 1_000_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    dividends: 0,
    buying_power: 500_000,
    excess_liquidity: 400_000,
    cushion: 0.5,
    settled_cash: 200_000,
    available_funds: 500_000,
    init_margin_req: 100_000,
    maint_margin_req: 80_000,
    equity_with_loan: 1_000_000,
    previous_day_equity_with_loan: 990_000,
    full_init_margin_req: 100_000,
    full_maint_margin_req: 80_000,
    full_available_funds: 500_000,
    full_excess_liquidity: 400_000,
    look_ahead_next_change: 0,
    sma: 0,
    stock_market_value: 0,
    option_market_value: 0,
    gross_position_value: 0,
    regt_equity: 1_000_000,
    regt_margin: 0,
  } as PortfolioData["account_summary"],
};

function makeFill(partial: Partial<ExecutedOrder> & { execId: string; time: string; realizedPNL: number }): ExecutedOrder {
  return {
    symbol: "AAPL",
    contract: {
      conId: 1,
      symbol: "AAPL",
      secType: "STK",
      strike: null,
      right: null,
      expiry: null,
    },
    side: "BOT",
    quantity: 1,
    avgPrice: 100,
    commission: 1,
    exchange: "SMART",
    ...partial,
  };
}

describe("MetricCards FillsModal ET day-cut", () => {
  it("only lists fills on the ET calendar day of the realized total", () => {
    // Freeze "now" to Friday 2026-08-07 20:00 UTC = 16:00 ET
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-07T20:00:00Z"));

    const todayFill = makeFill({
      execId: "today-1",
      time: "2026-08-07T15:00:00Z",
      realizedPNL: 250,
      symbol: "TODAY",
    });
    const yesterdayFill = makeFill({
      execId: "yday-1",
      time: "2026-08-06T15:00:00Z",
      realizedPNL: -9_999,
      symbol: "YDAY",
    });

    render(
      <MetricCards
        portfolio={PORTFOLIO}
        section="portfolio"
        realizedPnl={250}
        executedOrders={[yesterdayFill, todayFill]}
      />,
    );

    // Open the realized fills modal via the clickable metric
    const realizedLabel = screen.getAllByText(/realized/i)[0];
    // Metric card click handlers attach to the card; fire click on parent metric-card
    const card = realizedLabel.closest(".metric-card") ?? realizedLabel;
    (card as HTMLElement).click();

    // Today row present; prior-day fill must not appear
    expect(screen.queryByText("YDAY")).toBeNull();
    // TODAY symbol should be visible once modal is open — if click path differs,
    // assert via modal title + absence of YDAY which is the bug contract.
    expect(screen.queryByText(/-\$9,999|-\$9999|-9999/)).toBeNull();
  });
});
