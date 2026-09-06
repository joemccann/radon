/** @vitest-environment jsdom */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import ClearOverview, { AccountHistory } from "../components/dashboard/ClearOverview";
import { normalizePerformanceData } from "../lib/performanceData";
import type { PortfolioData } from "../lib/types";

vi.mock("@/lib/usePerformance", () => ({ usePerformance: () => ({ data: null, loading: false, error: null }) }));

const data = normalizePerformanceData({
  series: [
    { date: "2026-07-01", nav: 90_000, twr_index: 100 },
    { date: "2026-08-01", nav: 100_000, twr_index: 101 },
    { date: "2026-08-28", nav: 103_000, twr_index: 101.5 },
    { date: "2026-09-01", nav: 104_000, twr_index: 102 },
    { date: "2026-09-04", nav: 105_000, twr_index: 102.5 },
  ],
});

afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("Clear account history interaction", () => {
  it("changes the line, fill, axis dates and selected period together", () => {
    const { container } = render(<AccountHistory data={data} />);
    const before = Array.from(container.querySelectorAll("path")).slice(0, 2).map((path) => path.getAttribute("d"));
    expect(screen.getByText("Jul 1, 2026")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "1W", exact: true }));
    expect(screen.getByRole("button", { name: "1W", exact: true }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByText("Jul 1, 2026")).toBeNull();
    expect(screen.getByText("Aug 28, 2026")).toBeDefined();
    const after = Array.from(container.querySelectorAll("path")).slice(0, 2).map((path) => path.getAttribute("d"));
    expect(after[0]).not.toBe(before[0]);
    expect(after[1]).not.toBe(before[1]);
    expect(screen.getByRole("button", { name: "1Y", exact: true })).toHaveProperty("disabled", true);
  });

  it("provides keyboard inspection with exact dollar values, not index values", () => {
    render(<AccountHistory data={data} />);
    const chart = screen.getByRole("slider", { name: "Inspect account value history" });
    fireEvent.keyDown(chart, { key: "Home" });
    expect(chart.getAttribute("aria-valuetext")).toBe("Jul 1, 2026, $90,000");
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    expect(chart.getAttribute("aria-valuetext")).toBe("Aug 1, 2026, $100,000");
    fireEvent.keyDown(chart, { key: "End" });
    expect(chart.getAttribute("aria-valuetext")).toBe("Sep 4, 2026, $105,000");
  });

  it("retains a valid chart while a background fetch is unavailable", () => {
    render(<AccountHistory data={data} error="Source unreachable" />);
    expect(screen.getByRole("slider")).toBeDefined();
    expect(screen.queryByText("Account history unavailable")).toBeNull();
    expect(screen.getByText(/includes deposits and withdrawals/)).toBeDefined();
  });

  it("labels stale historical snapshots and clears inspection on blur", () => {
    render(<AccountHistory data={{ ...data!, status: "stale" }} />);
    expect(screen.getByText("Dated snapshot · Sep 4, 2026")).toBeDefined();
    const chart = screen.getByRole("slider");
    fireEvent.keyDown(chart, { key: "Home" });
    fireEvent.keyDown(chart, { key: "Escape" });
    expect(chart.getAttribute("aria-valuenow")).toBe("0");
    fireEvent.blur(chart);
    expect(chart.getAttribute("aria-valuenow")).toBe("4");
  });

  it("inspects observed points on pointer movement and restores the latest reading on exit", () => {
    render(<AccountHistory data={data} />);
    const chart = screen.getByRole("slider");
    vi.spyOn(chart, "getBoundingClientRect").mockReturnValue({ left: 0, width: 400, top: 0, right: 400, bottom: 138, height: 138, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent(chart, new MouseEvent("pointermove", { bubbles: true, clientX: 100 }));
    expect(chart.getAttribute("aria-valuenow")).toBe("1");
    fireEvent.pointerLeave(chart);
    expect(chart.getAttribute("aria-valuenow")).toBe("4");
  });

  it("explains an unmeasured NAV series and disables unsupported period buttons", () => {
    render(<AccountHistory data={normalizePerformanceData({ series: [{ date: "2026-09-04", twr_index: 100 }] })} />);
    expect(screen.getByText(/two verified dollar NAV/)).toBeDefined();
    for (const button of screen.getAllByRole("button")) expect(button).toHaveProperty("disabled", true);
  });

  it("reserves loading geometry and exposes missing history without fake curves", () => {
    const { rerender } = render(<AccountHistory data={null} loading />);
    expect(screen.getByText("Loading account history")).toBeDefined();
    expect(screen.queryByRole("slider")).toBeNull();
    rerender(<AccountHistory data={null} error="Unavailable" />);
    expect(screen.getByText("Account history unavailable")).toBeDefined();
    expect(screen.getByText(/could not be reached/)).toBeDefined();
    expect(screen.getByRole("link", { name: /View performance details/ }).getAttribute("href")).toBe("/performance");
  });
});

describe("Clear portfolio overview", () => {
  it("renders unavailable account and margin readings without reassuring defaults", () => {
    render(<ClearOverview portfolio={null} />);
    expect(screen.getByTestId("clear-account-value").textContent).toBe("---");
    expect(screen.getByText("Waiting for your portfolio")).toBeDefined();
    expect(screen.getAllByText("Margin data unavailable")).toHaveLength(2);
    expect(screen.queryByText("0.0%")).toBeNull();
  });

  it("keeps risk attention before positions and preserves position-specific links", () => {
    const portfolio = { positions: [{ id: 17, ticker: "XYZ", contracts: 2, expiry: "2026-12-18", structure: "Short Put", structure_type: "Short Put", entry_cost: -400, market_value: -300,
      legs: [{ type: "Put", direction: "SHORT", contracts: 2, strike: 100, entry_cost: 400, market_value: 300 }] }], undefined_risk_count: 1,
      last_sync: "2026-09-04T16:00:00Z", account_summary: { net_liquidation: 100_000.42, maintenance_margin: 90_000, excess_liquidity: 2_000, buying_power: 10_000 } } as PortfolioData;
    const { container } = render(<ClearOverview portfolio={portfolio} />);
    expect(screen.getByTestId("clear-account-value").textContent).toBe("$100,000.42");
    const risk = container.querySelector('a[href="#clear-risk-details"]')!;
    const positions = screen.getByRole("region", { name: /Your positions/ });
    expect(Boolean(risk.compareDocumentPosition(positions) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
    const positionLink = within(positions).getByRole("link", { name: /XYZ/ });
    expect(positionLink.getAttribute("href")).toBe("/XYZ?posId=17");
    expect(positionLink.textContent).toContain("+$100");
    expect(screen.getAllByText("Margin needs attention")).toHaveLength(2);
    expect(screen.getByRole("link", { name: /News, signals/ }).getAttribute("href")).toBe("#clear-market-intelligence");
  });

  it("retains the severity of a broker margin call on the mobile risk entry point", () => {
    const { container } = render(<ClearOverview portfolio={{ positions: [], undefined_risk_count: 0, account_summary: { net_liquidation: 100_000, excess_liquidity: -100, maintenance_margin: 20_000 } } as PortfolioData} />);
    expect(container.querySelector('a[href="#clear-risk-details"]')?.getAttribute("data-tone")).toBe("critical");
    expect(screen.getByText(/Margin call: Excess Liquidity/)).toBeDefined();
    expect(screen.getByText("No open positions")).toBeDefined();
    expect(screen.getAllByRole("link", { name: "Explore research", exact: true })).toHaveLength(2);
  });

  it.each([200, 0, -200])("retains a signed, current-session broker P&L of %s", (dailyPnl) => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-04T17:00:00Z"));
    const portfolio = { positions: [], undefined_risk_count: 0, last_sync: "2026-09-04T16:00:00Z", account_summary: { net_liquidation: 100_000, daily_pnl: dailyPnl, maintenance_margin: 20_000, excess_liquidity: 60_000 } } as PortfolioData;
    render(<ClearOverview portfolio={portfolio} />);
    const account = screen.getByRole("region", { name: "Your account" });
    expect(within(account).getByText(dailyPnl > 0 ? "+$200" : dailyPnl < 0 ? "−$200" : "$0")).toBeDefined();
    expect(within(account).getByText("Today's P&L")).toBeDefined();
  });
});
