// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import MetricCards from "../components/MetricCards";
import type { PortfolioData } from "../lib/types";

const make = (dividends: number): PortfolioData => ({
  bankroll: 100_000, peak_value: 100_000, last_sync: "2026-09-04T18:00:00Z",
  total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
  position_count: 0, defined_risk_count: 0, undefined_risk_count: 0,
  avg_kelly_optimal: null, exposure: {}, violations: [], positions: [],
  account_summary: { net_liquidation: 100_000, daily_pnl: 0, unrealized_pnl: 0, realized_pnl: 0,
    settled_cash: 90_000, maintenance_margin: 10_000, excess_liquidity: 90_000, buying_power: 360_000, dividends },
});
afterEach(cleanup);

describe("portfolio KPI ledger: no dead tile", () => {
  it("drops the Dividends tile when nothing is accrued", () => {
    render(<MetricCards portfolio={make(0)} section="portfolio" />);
    expect(screen.queryByRole("button", { name: "View Dividends breakdown" })).toBeNull();
    expect(screen.getByRole("button", { name: "View Net Liquidation breakdown" }).className).toContain("metric-card-hero");
  });
  it("keeps the Dividends tile when dividends are accrued", () => {
    render(<MetricCards portfolio={make(12)} section="portfolio" />);
    expect(screen.getByRole("button", { name: "View Dividends breakdown" })).toBeDefined();
  });
});
