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

  it("uses a dedicated compact module, not orphaned section aliases", () => {
    render(
      <CorrelationRiskBanner
        report={report({ clusters: [BREACH], breaches: [BREACH] })}
      />,
    );
    const banner = screen.getByTestId("correlation-risk-banner");
    expect(banner.classList.contains("crb")).toBe(true);
    expect(banner.classList.contains("sx")).toBe(false);
    expect(banner.querySelector(".s-hd")).toBeNull();
    expect(banner.querySelector(".s-tt")).toBeNull();
    expect(banner.querySelector(".crb-header")).toBeTruthy();
    expect(banner.querySelector(".crb-title")?.textContent).toMatch(/Correlation Risk Budget/);
    expect(banner.querySelector(".crb-gate")?.textContent).toMatch(/GATE 3/);
  });

  it("never renders a calm level while positions are unmeasured", () => {
    const CALM = { ...BREACH, aggregate_exposure: 0.01, breached: false };
    render(
      <CorrelationRiskBanner
        report={report({
          clusters: [CALM],
          breaches: [],
          insufficient_data: ["THIN", "NEW"],
        })}
      />,
    );
    const banner = screen.getByTestId("correlation-risk-banner");
    const level = banner.getAttribute("data-level");
    expect(["none", "info"]).not.toContain(level);
    expect(level).toBe("unmeasured");
    expect(banner.textContent).not.toMatch(/within budget/);
    expect(banner.textContent).toMatch(/2 positions unmeasured/);
    expect(screen.getByTestId("crb-insufficient-data").textContent).toContain("THIN");
  });

  // Was asserted as level "info" via the component's `showUnavailable` path.
  // R-283 moved the decision into `correlationRiskBanner`, so an unmeasured
  // book now reports the same `unmeasured` level as its sibling case above
  // instead of two different levels for one condition. The original intent —
  // this case renders a Gate-3 module rather than nothing, and names the
  // tickers it could not measure — is what is asserted here.
  it("renders an unmeasured book as a Gate-3 module naming the tickers", () => {
    render(
      <CorrelationRiskBanner
        report={report({ insufficient_data: ["ADBE", "CBRS", "META"] })}
        showUnavailable
      />,
    );
    const banner = screen.getByTestId("correlation-risk-banner");
    expect(banner.getAttribute("data-level")).toBe("unmeasured");
    expect(banner.textContent).not.toMatch(/no correlated concentration/i);
    expect(banner.textContent).toMatch(/3 positions/);
    const chips = Array.from(banner.querySelectorAll(".crb-ticker")).map((el) => el.textContent);
    expect(chips).toEqual(["ADBE", "CBRS", "META"]);
  });

  it("still renders the unavailable module when there is no report at all", () => {
    render(<CorrelationRiskBanner report={null} showUnavailable />);
    const banner = screen.getByTestId("correlation-risk-banner");
    expect(banner.textContent).toMatch(/Gate 3: correlation measurement unavailable/);
  });
});
