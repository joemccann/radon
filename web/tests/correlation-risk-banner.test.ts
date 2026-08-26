/**
 * Unit tests: Portfolio correlation risk-budget Gate-3 banner (F8).
 *
 * Pure derivation from the Python `build_risk_budget_report` payload into
 * banner props. No DOM, no network — same offline contract as the Python core.
 */

import { describe, it, expect } from "vitest";
import {
  correlationRiskBanner,
  type RiskBudgetReport,
} from "@/lib/correlationRiskBanner";

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

const BREACH_CLUSTER = {
  tickers: ["AAA", "BBB"],
  aggregate_exposure: 0.05,
  budget: 0.025,
  breached: true,
  max_pair_corr: 0.98,
  per_ticker_exposure: { AAA: 0.025, BBB: 0.025 },
};

describe("correlationRiskBanner", () => {
  it("returns null when there is no report", () => {
    expect(correlationRiskBanner(null)).toBeNull();
  });

  it("is calm (none) when no clusters breach the budget", () => {
    const banner = correlationRiskBanner(report({ clusters: [], breaches: [] }));
    expect(banner).not.toBeNull();
    expect(banner!.level).toBe("none");
    expect(banner!.gate).toBe(3);
    expect(banner!.breachedClusters).toHaveLength(0);
  });

  it("flags a Gate-3 breach when a correlated cluster exceeds budget", () => {
    const banner = correlationRiskBanner(
      report({ clusters: [BREACH_CLUSTER], breaches: [BREACH_CLUSTER] }),
    );
    expect(banner!.level).toBe("critical");
    expect(banner!.gate).toBe(3);
    expect(banner!.breachedClusters).toHaveLength(1);
    expect(banner!.breachedClusters[0].tickers).toEqual(["AAA", "BBB"]);
    // Aggregate exposure rendered as a percent string with no em dash / hex.
    expect(banner!.headline).toContain("Gate 3");
    expect(banner!.headline).not.toContain("—");
  });

  it("surfaces a soft notice when correlated clusters exist but stay in budget", () => {
    const calmCluster = { ...BREACH_CLUSTER, aggregate_exposure: 0.01, breached: false };
    const banner = correlationRiskBanner(
      report({ clusters: [calmCluster], breaches: [] }),
    );
    expect(banner!.level).toBe("info");
    expect(banner!.clusterCount).toBe(1);
  });

  it("refuses a clean within-budget verdict while positions are unmeasured", () => {
    const calmCluster = { ...BREACH_CLUSTER, aggregate_exposure: 0.01, breached: false };
    const banner = correlationRiskBanner(
      report({
        clusters: [calmCluster],
        breaches: [],
        insufficient_data: ["THIN", "NEW"],
      }),
    );
    // A gate that reads clean on exposure it never measured is a gate failure.
    expect(banner!.level).toBe("unmeasured");
    expect(banner!.headline).not.toMatch(/within budget/);
    expect(banner!.headline).toContain("Gate 3");
    expect(banner!.headline).toContain("2");
    expect(banner!.headline).not.toContain("\u2014");
    expect(banner!.insufficientData).toEqual(["THIN", "NEW"]);
    expect(banner!.clusterCount).toBe(1);
  });

  it("notes insufficient-data tickers without crashing", () => {
    const banner = correlationRiskBanner(
      report({ insufficient_data: ["THIN", "NEW"] }),
    );
    expect(banner!.insufficientData).toEqual(["THIN", "NEW"]);
  });

  it("formats cluster exposure as a percent of book", () => {
    const banner = correlationRiskBanner(
      report({ clusters: [BREACH_CLUSTER], breaches: [BREACH_CLUSTER] }),
    );
    expect(banner!.breachedClusters[0].exposurePctLabel).toBe("5.0%");
    expect(banner!.breachedClusters[0].budgetPctLabel).toBe("2.5%");
  });
});
