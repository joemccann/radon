// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import MetricCards from "../components/MetricCards";
import type { PortfolioData } from "../lib/types";

const portfolio: PortfolioData = {
  bankroll: 100_000, peak_value: 100_000, last_sync: "2026-09-04T18:00:00Z",
  total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
  position_count: 0, defined_risk_count: 0, undefined_risk_count: 0,
  avg_kelly_optimal: null, exposure: {}, violations: [], positions: [],
  account_summary: {
    net_liquidation: 100_000, daily_pnl: 0, unrealized_pnl: 0, realized_pnl: 0,
    settled_cash: 90_000, maintenance_margin: 10_000, excess_liquidity: 90_000,
    buying_power: 360_000, dividends: 12,
  },
};

afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("MetricCards keyboard access", () => {
  it.each(["Enter", " "])("opens account details with %j and identifies the value", (key) => {
    render(<MetricCards portfolio={portfolio} section="portfolio" />);
    const card = screen.getByRole("button", { name: "View Net Liquidation breakdown" });
    expect(card.tabIndex).toBe(0);
    expect(card.getAttribute("aria-haspopup")).toBe("dialog");
    const description = card.getAttribute("aria-describedby")!.split(" ").map((id) => document.getElementById(id)?.textContent).join(" ");
    expect(description).toContain("$100,000.00");
    expect(description).toContain("BANKROLL");
    card.focus();
    expect(fireEvent.keyDown(card, { key })).toBe(false);
    expect(screen.getByRole("dialog", { name: "Net Liquidation Value" })).toBeTruthy();
  });

  it.each(["Enter", " "])("toggles a section with %j and reports its expanded state", (key) => {
    render(<MetricCards portfolio={portfolio} section="portfolio" />);
    const risk = screen.getByRole("button", { name: "RISK metrics" });
    expect(risk.tabIndex).toBe(0);
    expect(risk.getAttribute("aria-expanded")).toBe("false");
    fireEvent.keyDown(risk, { key });
    expect(risk.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: "View Buying Power breakdown" })).toBeTruthy();
    fireEvent.keyDown(risk, { key });
    expect(risk.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("button", { name: "View Buying Power breakdown" })).toBeNull();
  });

  it("does not activate on unrelated or repeated keys", () => {
    render(<MetricCards portfolio={portfolio} section="portfolio" />);
    const account = screen.getByRole("button", { name: "ACCOUNT metrics" });
    fireEvent.keyDown(account, { key: "ArrowDown" });
    fireEvent.keyDown(account, { key: " ", repeat: true });
    expect(account.getAttribute("aria-expanded")).toBe("true");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does not duplicate an activation that originated inside a child element", () => {
    render(<MetricCards portfolio={portfolio} section="portfolio" />);
    const account = screen.getByRole("button", { name: "ACCOUNT metrics" });
    fireEvent.keyDown(account.querySelector("span")!, { key: "Enter" });
    expect(account.getAttribute("aria-expanded")).toBe("true");
  });

  it("preserves pointer drill-down and keeps noninteractive placeholders outside tab order", () => {
    const { container } = render(<MetricCards portfolio={portfolio} section="portfolio" />);
    const placeholders = container.querySelectorAll(".metric-card:not(.metric-card-clickable)");
    expect(placeholders.length).toBeGreaterThan(0);
    for (const placeholder of placeholders) {
      expect(placeholder.getAttribute("role")).toBeNull();
      expect(placeholder.getAttribute("tabindex")).toBeNull();
    }
    fireEvent.click(screen.getByRole("button", { name: "View Dividends breakdown" }));
    expect(screen.getByRole("dialog", { name: "Accrued Dividends" })).toBeTruthy();
  });

  it("supports the closed-market Realized drill-down through the same keyboard contract", () => {
    render(<MetricCards portfolio={portfolio} section="portfolio" realizedPnl={125} />);
    const realized = screen.getByRole("button", { name: "View Realized breakdown" });
    fireEvent.keyDown(realized, { key: " " });
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});
