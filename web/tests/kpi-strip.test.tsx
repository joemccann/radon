/**
 * @vitest-environment jsdom
 *
 * KPI telemetry strip — desktop 4-cell grid driven by deriveKpis; meters only
 * render when their inputs exist; tokens only.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import { KpiStrip } from "@/components/dashboard/KpiStrip";
import type { PortfolioData } from "@/lib/types";

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true }),
}));

afterEach(() => cleanup());

const portfolio = {
  bankroll: 0,
  peak_value: 0,
  last_sync: "2026-08-07T16:35:18-04:00",
  positions: [],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 0,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  account_summary: {
    net_liquidation: 2_847_120,
    daily_pnl: 18_432,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 0,
    maintenance_margin: 894_000,
    excess_liquidity: 1_100_000,
    buying_power: 1_204_580,
    dividends: 0,
    available_funds: 900_000,
    equity_with_loan: 3_000_000,
  },
} as PortfolioData;

describe("KpiStrip", () => {
  it("renders the four account cells with exact figures", () => {
    render(<KpiStrip portfolio={portfolio} realizedPnl={0} />);
    expect(screen.getByText("Net Liquidation")).toBeTruthy();
    expect(screen.getByText("$2,847,120")).toBeTruthy();
    expect(screen.getByText("Today P&L")).toBeTruthy();
    expect(screen.getByText("+$18,432")).toBeTruthy();
    expect(screen.getByText("Buying Power")).toBeTruthy();
    expect(screen.getByText("Margin Used")).toBeTruthy();
    expect(screen.getByText(`${((894_000 / 2_847_120) * 100).toFixed(1)}%`)).toBeTruthy();
  });

  it("renders meters for buying power and margin only", () => {
    const { container } = render(<KpiStrip portfolio={portfolio} realizedPnl={0} />);
    expect(container.querySelectorAll(".dashboard-kpi__bar")).toHaveLength(2);
  });

  it("renders placeholders without portfolio data", () => {
    render(<KpiStrip portfolio={null} realizedPnl={0} />);
    expect(screen.getAllByText("—")).toHaveLength(4);
    expect(document.querySelectorAll(".dashboard-kpi__bar")).toHaveLength(0);
  });

  it("uses brand tokens only — no raw hex in rendered markup", () => {
    const { container } = render(<KpiStrip portfolio={portfolio} realizedPnl={0} />);
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
