/**
 * Gate helpers and gateCopy — spec §A.5, §E.9.
 *
 * Successor to the v1 suite that pinned `annualizedTwr` (the 252/N annualizer,
 * defect #4's TypeScript twin), `isBetaGated` and `isVarGated`. Those exports
 * are deleted; the scenarios they covered are re-pinned here against the v2
 * gate table:
 *
 *   v1 pin                       v2 expression
 *   ---------------------------  ---------------------------------------------
 *   annualized N gate (N>=20)    annualization gates on CALENDAR DAYS >= 365
 *   beta gated when N<40         isBetaGated -> isBenchmarkGated(nCommon, N)
 *   VaR gated when N<60          isVarGated  -> isDispersionGated (20) + low_confidence
 *   methodology curve_type       payload-level, pinned in performance-payload-contract
 *   isPerformanceBehindPortfolioSync   unchanged, carried over verbatim
 */

import { describe, it, expect } from "vitest";
import {
  TWR_GATES,
  TWR_CONVENTIONS,
  gateCopy,
  isAnnualizationGated,
  isBenchmarkGated,
  isDispersionGated,
  isImplausibleAlpha,
  isImplausibleAnnualized,
  isLowConfidenceRatio,
  isLowConfidenceVar,
  isMwrGated,
  isRatioGated,
  published,
  suppressed,
  type GateReason,
  type GatedValue,
} from "../lib/performanceTwr";
import { isPerformanceBehindPortfolioSync } from "../lib/performanceFreshness";

// ---------------------------------------------------------------------------
// The gate table itself
// ---------------------------------------------------------------------------

describe("TWR_GATES", () => {
  it("separates the two day counts", () => {
    // 252 scales volatility and ratios; 365 (Act/365 Fixed) annualizes returns
    // and discounts XIRR. Conflating them is how a 79-day window became 5.2e6%.
    expect(TWR_GATES.TRADING_DAYS).toBe(252);
    expect(TWR_GATES.DAYS_PER_YEAR).toBe(365);
  });

  it("declares the flow convention it was computed under", () => {
    expect(TWR_CONVENTIONS.FLOW_CONVENTION).toBe("eod");
    expect(TWR_CONVENTIONS.SORTINO_TARGET).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Annualization — was N>=20, now a calendar-days gate
// ---------------------------------------------------------------------------

describe("annualization gate", () => {
  it("refuses to annualize a sub-year period regardless of sample size", () => {
    // The live defect: 79 calendar days, 57 returns, cum_r 9.5128.
    expect(isAnnualizationGated(79)).toBe(true);
    expect(isAnnualizationGated(364)).toBe(true);
  });

  it("opens at exactly one year", () => {
    expect(isAnnualizationGated(TWR_GATES.MIN_CALENDAR_DAYS_ANNUALIZED)).toBe(false);
    expect(isAnnualizationGated(366)).toBe(false);
  });

  it("flags an annualized return past 1000%/yr as a data defect", () => {
    expect(isImplausibleAnnualized(0.42)).toBe(false);
    expect(isImplausibleAnnualized(TWR_GATES.IMPLAUSIBLE_ANNUALIZED)).toBe(false);
    expect(isImplausibleAnnualized(32889.5462)).toBe(true); // the live render
    expect(isImplausibleAnnualized(-32889.5462)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Dispersion / ratio / MWR gates
// ---------------------------------------------------------------------------

describe("sample-size gates", () => {
  it("gates dispersion at 20", () => {
    expect(isDispersionGated(TWR_GATES.MIN_N_DISPERSION - 1)).toBe(true);
    expect(isDispersionGated(TWR_GATES.MIN_N_DISPERSION)).toBe(false);
  });

  it("gates sharpe and sortino at 60, not 20", () => {
    // SE of an annualized Sharpe is ~sqrt(252/N); at N=20 that is +/-3.5.
    expect(isRatioGated(20)).toBe(true);
    expect(isRatioGated(TWR_GATES.MIN_N_RATIO - 1)).toBe(true);
    expect(isRatioGated(TWR_GATES.MIN_N_RATIO)).toBe(false);
  });

  it("gates MWR at 20", () => {
    expect(isMwrGated(TWR_GATES.MIN_N_MWR - 1)).toBe(true);
    expect(isMwrGated(TWR_GATES.MIN_N_MWR)).toBe(false);
    expect(isMwrGated(57)).toBe(false); // the live "needs 20 sessions (N=57)" card
  });

  it("marks low confidence without withholding the number", () => {
    expect(isLowConfidenceVar(TWR_GATES.VAR_LOW_CONFIDENCE_N - 1)).toBe(true);
    expect(isLowConfidenceVar(TWR_GATES.VAR_LOW_CONFIDENCE_N)).toBe(false);
    expect(isLowConfidenceRatio(TWR_GATES.RATIO_LOW_CONFIDENCE_N - 1)).toBe(true);
    expect(isLowConfidenceRatio(TWR_GATES.RATIO_LOW_CONFIDENCE_N)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Benchmark gate — on the ALIGNED sample, not the portfolio's
// ---------------------------------------------------------------------------

describe("benchmark gate", () => {
  it("gates on n_common, not n_returns", () => {
    // The live payload: a portfolio with 57 returns and a benchmark that
    // covered almost none of them still rendered beta = 23.93.
    expect(isBenchmarkGated(4, 57)).toBe(true);
    expect(isBenchmarkGated(TWR_GATES.MIN_N_BENCHMARK - 1, 40)).toBe(true);
    expect(isBenchmarkGated(TWR_GATES.MIN_N_BENCHMARK, 40)).toBe(false);
  });

  it("gates on coverage even when n_common clears the floor", () => {
    // 50 aligned of 60 portfolio sessions = 0.833 coverage
    expect(isBenchmarkGated(50, 60)).toBe(true);
    expect(isBenchmarkGated(54, 60)).toBe(false); // 0.90 exactly
    expect(TWR_GATES.BENCHMARK_MIN_COVERAGE).toBe(0.9);
  });

  it("flags alpha past 100%/yr as a data defect", () => {
    expect(isImplausibleAlpha(0.12)).toBe(false);
    expect(isImplausibleAlpha(TWR_GATES.IMPLAUSIBLE_ALPHA)).toBe(false);
    expect(isImplausibleAlpha(21.9009)).toBe(true); // the live ALPHA +2190.09%
  });
});

// ---------------------------------------------------------------------------
// gateCopy — the only producer of unavailable-metric copy
// ---------------------------------------------------------------------------

const ALL_REASONS: GateReason[] = [
  "insufficient_n",
  "period_lt_1y",
  "no_downside",
  "benchmark_unavailable",
  "benchmark_coverage",
  "benchmark_degenerate",
  "not_computed",
  "no_flow_data",
  "no_convergence",
  "no_sign_change",
  "degenerate",
  "insufficient_dates",
  "total_loss",
  "implausible",
  "invalid_total_return",
  "nav_unavailable",
];

describe("gateCopy", () => {
  it("produces non-empty copy for every reason in the union", () => {
    for (const reason of ALL_REASONS) {
      const copy = gateCopy(suppressed(reason, 12, 20), "Sharpe");
      expect(copy, reason).toBeTruthy();
      expect(copy.length, reason).toBeGreaterThan(0);
    }
  });

  it("returns empty copy when a value is present, so no card pairs a number with an excuse", () => {
    expect(gateCopy(published(1.97, 60, 60), "Sharpe")).toBe("");
    expect(gateCopy(published(0, 60, 60), "Hit Rate")).toBe("");
  });

  it("names the sample size only for an actual sample-size gate", () => {
    expect(gateCopy(suppressed("insufficient_n", 19, 20), "MWR")).toBe(
      "needs 20 sessions (N=19)",
    );
  });

  it("blames flows, not N, when the flow fetch failed", () => {
    // The live card read "needs 20 sessions (N=57)" for a metric that was
    // never computed at all.
    const copy = gateCopy(
      { value: null, n: 57, min_n: 20, unavailable_reason: "no_flow_data" },
      "MWR IRR",
    );
    expect(copy).toBe("external flows unavailable");
    expect(copy).not.toContain("sessions (N=");
  });

  it("blames the period, not N, for a sub-year annualization", () => {
    const copy = gateCopy(
      { value: null, n: 57, min_n: 1, unavailable_reason: "period_lt_1y" },
      "Annualized",
    );
    expect(copy).toBe("needs 1 year of history");
    expect(copy).not.toContain("sessions (N=");
  });

  it("distinguishes the three benchmark failure modes", () => {
    expect(gateCopy(suppressed("benchmark_unavailable", 0, 40), "Beta")).toBe(
      "SPY series unavailable",
    );
    expect(gateCopy(suppressed("benchmark_degenerate", 60, 40), "Beta")).toBe(
      "SPY series has no variance",
    );
    expect(gateCopy(suppressed("benchmark_coverage", 30, 40), "Beta")).toContain("SPY covers");
  });

  it("falls back to a labelled reason for an absent reason string", () => {
    const bare: GatedValue = { value: null, n: 0, min_n: 20, unavailable_reason: null };
    expect(gateCopy(bare, "Sortino")).toBe("Sortino not computed");
  });
});

// ---------------------------------------------------------------------------
// GatedValue constructors
// ---------------------------------------------------------------------------

describe("GatedValue constructors", () => {
  it("suppressed always carries a reason", () => {
    const g = suppressed("insufficient_n", 5, 20);
    expect(g.value).toBeNull();
    expect(g.unavailable_reason).toBe("insufficient_n");
  });

  it("published never carries a reason", () => {
    const g = published(0.42, 60, 20, true);
    expect(g.value).toBe(0.42);
    expect(g.unavailable_reason).toBeNull();
    expect(g.low_confidence).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// isPerformanceBehindPortfolioSync — unchanged by the refactor
// ---------------------------------------------------------------------------

describe("isPerformanceBehindPortfolioSync", () => {
  it("returns false when performance matches portfolio last_sync", () => {
    const lastSync = "2026-05-09T16:00:00Z";
    const perf = { as_of: "2026-05-09", last_sync: lastSync };
    expect(isPerformanceBehindPortfolioSync(perf as never, lastSync)).toBe(false);
  });

  it("returns true when portfolio has advanced past performance", () => {
    const perf = { as_of: "2026-05-08", last_sync: "2026-05-08T16:00:00Z" };
    expect(isPerformanceBehindPortfolioSync(perf as never, "2026-05-09T16:00:00Z")).toBe(true);
  });

  it("returns true when last_sync mismatches even if as_of matches", () => {
    const perf = { as_of: "2026-05-09", last_sync: "2026-05-09T14:00:00Z" };
    expect(isPerformanceBehindPortfolioSync(perf as never, "2026-05-09T16:00:00Z")).toBe(true);
  });

  it("returns false when portfolioLastSync is missing", () => {
    const perf = { as_of: "2026-05-09", last_sync: "2026-05-09T16:00:00Z" };
    expect(isPerformanceBehindPortfolioSync(perf as never, null)).toBe(false);
    expect(isPerformanceBehindPortfolioSync(perf as never, undefined)).toBe(false);
  });

  it("returns false when performance is missing", () => {
    expect(isPerformanceBehindPortfolioSync(null, "2026-05-09T16:00:00Z")).toBe(false);
    expect(isPerformanceBehindPortfolioSync(undefined, "2026-05-09T16:00:00Z")).toBe(false);
  });

  it("handles naive UTC timestamps via ET conversion (midnight drift guard)", () => {
    const naiveSync = "2026-05-09T01:00:00.123456";
    const perf = { as_of: "2026-05-09", last_sync: "2026-05-09T16:00:00Z" };
    expect(isPerformanceBehindPortfolioSync(perf as never, naiveSync)).toBe(true);
  });

  it("advances across polls", () => {
    const perf = { as_of: "2026-05-09", last_sync: "2026-05-09T10:00:00Z" };
    expect(isPerformanceBehindPortfolioSync(perf as never, "2026-05-09T10:00:00Z")).toBe(false);
    expect(isPerformanceBehindPortfolioSync(perf as never, "2026-05-10T10:00:00Z")).toBe(true);
  });
});
