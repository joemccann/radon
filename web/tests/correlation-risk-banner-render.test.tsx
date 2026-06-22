/**
 * @vitest-environment jsdom
 *
 * Component test: CorrelationRiskBanner renders the Gate-3 verdict from a
 * risk-budget report and stays hidden when there is no concentration.
 */

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import CorrelationRiskBanner from "../components/CorrelationRiskBanner";
import type { RiskBudgetReport } from "../lib/correlationRiskBanner";

afterEach(cleanup);

function report(overrides: Partial<RiskBudgetReport> = {}): RiskBudgetReport {
  return {
    clusters: [],
    breaches: [],
    aggregate_exposure: 0,
    insufficient_data: [],
    corr_threshold: 0.7,
    book_budget: 0.025,
    ...overrides,
  };
}

const BREACH = {
  tickers: ["AAA", "BBB"],
  aggregate_exposure: 0.05,
  budget: 0.025,
  breached: true,
  max_pair_corr: 0.98,
  per_ticker_exposure: { AAA: 0.025, BBB: 0.025 },
};

describe("CorrelationRiskBanner", () => {
  it("renders nothing when there is no report", () => {
    const { container } = render(<CorrelationRiskBanner report={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when there is no concentration (level none)", () => {
    const { container } = render(<CorrelationRiskBanner report={report()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a critical Gate-3 breach with cluster rows", () => {
    render(
      <CorrelationRiskBanner
        report={report({ clusters: [BREACH], breaches: [BREACH] })}
      />,
    );
    const banner = screen.getByTestId("correlation-risk-banner");
    expect(banner.getAttribute("data-level")).toBe("critical");
    expect(screen.getByText(/Gate 3/)).toBeTruthy();
    expect(screen.getByText(/AAA \+ BBB/)).toBeTruthy();
    expect(screen.getByText(/5.0% vs 2.5% budget/)).toBeTruthy();
  });

  it("lists insufficient-data tickers", () => {
    render(
      <CorrelationRiskBanner
        report={report({
          clusters: [BREACH],
          breaches: [BREACH],
          insufficient_data: ["THIN"],
        })}
      />,
    );
    expect(screen.getByTestId("crb-insufficient-data").textContent).toContain("THIN");
  });
});
