/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import StrengthConfirmationScanner from "../components/StrengthConfirmationScanner";
import type { StrengthConfirmationData, StrengthFactorAssessment } from "../lib/types";

vi.mock("@/lib/useTickerNav", () => ({
  useTickerNav: () => ({
    navigateToTicker: vi.fn(),
  }),
}));

afterEach(() => {
  cleanup();
});

const groups = [
  "Q-SCORES",
  "NET GEX",
  "CALL POSITIONING",
  "TERM STRUCTURE",
  "VOLATILITY SMILE",
  "SYSTEMATIC POSITIONING",
  "MARKET BREADTH",
];

function factor(group: string, passed = true, source: "UW" | "APPROX" = "UW"): StrengthFactorAssessment {
  return {
    group,
    passed,
    checks_passed: passed ? 3 : 2,
    checks_total: 3,
    source,
    notes: source === "APPROX" ? ["Approximation"] : [],
    checks: [
      { label: `${group} check`, passed, value: passed ? 1 : 0, threshold: "pass", note: "Measured", source },
    ],
  };
}

const data: StrengthConfirmationData = {
  scan_time: "2026-06-24T15:00:00Z",
  source: "Unusual Whales + Radon regime caches",
  universe: "fallback:ndx100",
  tickers_scanned: 2,
  candidates_found: 2,
  confirmed_strength_count: 1,
  results: [
    {
      ticker: "AAPL",
      verdict: "REAL_STRENGTH_CONFIRMED",
      score: 100,
      groups_passed: 7,
      spot: 212.4,
      factors: groups.map((group) => factor(group, true, group === "SYSTEMATIC POSITIONING" ? "APPROX" : "UW")),
      errors: [],
    },
    {
      ticker: "MSFT",
      verdict: "WATCHLIST",
      score: 86,
      groups_passed: 6,
      spot: 486.1,
      factors: groups.map((group) => factor(group, group !== "TERM STRUCTURE", group === "SYSTEMATIC POSITIONING" ? "APPROX" : "UW")),
      errors: [],
    },
  ],
};

describe("StrengthConfirmationScanner", () => {
  it("renders desktop factor rows, mobile cards, and scan actions", () => {
    const onScan = vi.fn();
    const onTickerScan = vi.fn();
    render(
      <StrengthConfirmationScanner
        data={data}
        onScan={onScan}
        onTickerScan={onTickerScan}
        lastSync={data.scan_time}
      />,
    );

    const section = screen.getByTestId("strength-confirmation-section");
    expect(within(section).getByText("7-Step Strength")).toBeTruthy();
    expect(within(section).getByText("1 CONFIRMED")).toBeTruthy();
    expect(within(section).getAllByText("AAPL").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("REAL STRENGTH").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("100").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("TERM").length).toBeGreaterThan(0);
    expect(within(section).getByText("FAILED: NONE")).toBeTruthy();
    expect(within(section).getAllByText("TERM").some((node) => node.closest(".strength-factor-chip"))).toBe(true);

    const mobileList = screen.getByTestId("strength-confirmation-mobile-list");
    expect(within(mobileList).getAllByText("AAPL").length).toBeGreaterThan(0);
    expect(within(mobileList).getAllByText("CONFIRMATION SCORE").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /scan ndx/i }));
    expect(onScan).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText("Strength ticker symbol"), { target: { value: "mu" } });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));
    expect(onTickerScan).toHaveBeenCalledWith("MU");
  });

  it("rejects malformed ticker search text", () => {
    const onTickerScan = vi.fn();
    render(<StrengthConfirmationScanner data={data} onTickerScan={onTickerScan} />);

    fireEvent.change(screen.getByLabelText("Strength ticker symbol"), { target: { value: "MU1" } });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    expect(onTickerScan).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("Enter 1-6 letters.");
  });

  it("renders the measured-empty state when no names pass", () => {
    render(
      <StrengthConfirmationScanner
        data={{ ...data, confirmed_strength_count: 0, candidates_found: 0, results: [] }}
      />,
    );

    expect(screen.getByText("No confirmed strength setups")).toBeTruthy();
    expect(screen.getByText("The latest scan did not find names with all seven factor groups aligned.")).toBeTruthy();
  });
});
