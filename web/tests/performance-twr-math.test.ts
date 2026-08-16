/**
 * TS half of the single gate table (§A.3 / §A.5) and the copy generator (§A.5).
 *
 * RED-first. `web/lib/performanceTwr.ts` today exports ad-hoc, mutually
 * contradictory gates (annualized at N>=20 with a 252/N exponent, beta at 40,
 * VaR at 60) with no shared table and no copy generator. The spec replaces all
 * of it with one exported constant object mirrored from
 * `scripts/lib/twr_gates.py`, one predicate per metric family, and one
 * exhaustive `gateCopy`.
 *
 * Acceptance: F8 (one gate table), F6 (no N-gate copy for a non-N reason).
 */
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { describe, expect, it } from "vitest";
import * as twr from "../lib/performanceTwr";

const PERFORMANCE_TWR_SOURCE = readFileSync(
  fileURLToPath(new URL("../lib/performanceTwr.ts", import.meta.url)),
  "utf-8",
);

const GATES_PY_PATH = fileURLToPath(new URL("../../scripts/lib/twr_gates.py", import.meta.url));

type GateModule = typeof twr & {
  TWR_GATES: Record<string, number>;
  TWR_CONVENTIONS: Record<string, string | number>;
  gateCopy: (g: GatedValue, label: string) => string;
  isDispersionGated: (n: number) => boolean;
  isBenchmarkGated: (nCommon: number, nReturns: number) => boolean;
  isRatioGated: (n: number) => boolean;
  isMwrGated: (n: number) => boolean;
  isAnnualizationGated: (calendarDays: number) => boolean;
  isLowConfidenceVar: (n: number) => boolean;
  isLowConfidenceRatio: (n: number) => boolean;
  isImplausibleAnnualized: (value: number) => boolean;
  isImplausibleAlpha: (value: number) => boolean;
};

type GatedValue = {
  value: number | null;
  n: number;
  min_n: number;
  unavailable_reason: string | null;
  low_confidence?: boolean;
};

const mod = twr as unknown as GateModule;

function gate(
  n: number,
  min_n: number,
  unavailable_reason: string | null,
  value: number | null = null,
): GatedValue {
  return { value, n, min_n, unavailable_reason };
}

// ---------------------------------------------------------------------------
// §A.3 / §A.5 — the single gate table
// ---------------------------------------------------------------------------

/** Exact mirror of scripts/lib/twr_gates.py. Any drift is a build failure. */
const EXPECTED_GATES: Record<string, number> = {
  TRADING_DAYS: 252,
  DAYS_PER_YEAR: 365,
  MIN_N_CHAIN: 1,
  MIN_N_DISPERSION: 20,
  MIN_N_BENCHMARK: 40,
  MIN_N_RATIO: 60,
  MIN_N_MWR: 20,
  MIN_DOWNSIDE_OBSERVATIONS: 5,
  MIN_CALENDAR_DAYS_ANNUALIZED: 365,
  BENCHMARK_MIN_COVERAGE: 0.9,
  VAR_LOW_CONFIDENCE_N: 100,
  RATIO_LOW_CONFIDENCE_N: 252,
  SUSPECT_RETURN_THRESHOLD: 0.5,
  // DECISION 2: the dispersion bar is max(median|r| * MULTIPLE, FLOOR) and can
  // never switch itself off, so both halves are gates and both are mirrored.
  UNEXPLAINED_OUTLIER_MULTIPLE: 5,
  // The bar is max(median * 5, p95 * 3, floor). The tail term may only ever
  // WIDEN it: a median multiple describes a typical session, not an impossible
  // one, so on a volatile book the median-only bar sat inside the account's own
  // upper tail and quarantined ordinary trading days forever.
  UNEXPLAINED_TAIL_QUANTILE: 0.95,
  UNEXPLAINED_TAIL_MULTIPLE: 3,
  UNEXPLAINED_ABSOLUTE_FLOOR: 0.1,
  FLOW_DOMINANT_RATIO: 0.25,
  IMPLAUSIBLE_ANNUALIZED: 10,
  IMPLAUSIBLE_ALPHA: 1,
  MAX_SUBPERIOD_GAP_DAYS: 4,
  NAV_STALENESS_BUDGET_SESSIONS: 2,
};

describe("TWR_GATES table", () => {
  it("exports every gate with the spec value and no extras", () => {
    expect(mod.TWR_GATES).toBeDefined();
    expect(Object.keys(mod.TWR_GATES).sort()).toEqual(Object.keys(EXPECTED_GATES).sort());
    for (const [key, expected] of Object.entries(EXPECTED_GATES)) {
      expect(mod.TWR_GATES[key], `TWR_GATES.${key}`).toBe(expected);
    }
  });

  it("exports the non-numeric conventions separately", () => {
    expect(mod.TWR_CONVENTIONS).toBeDefined();
    // BOD: r_t = (E_t - C_t - B_t) / (B_t + C_t), denominator = B_t + C_t (§B.3).
    // The denominator is the capital actually at work, which is what IBKR
    // PortfolioAnalyst reports against.
    expect(mod.TWR_CONVENTIONS.FLOW_CONVENTION).toBe("bod");
    expect(mod.TWR_CONVENTIONS.SORTINO_TARGET).toBe(0);
  });

  it("carries the mirror warning header so an editor sees the parity contract", () => {
    expect(PERFORMANCE_TWR_SOURCE).toContain("MIRROR OF scripts/lib/twr_gates.py");
  });

  it("matches scripts/lib/twr_gates.py value for value", () => {
    const py = readFileSync(GATES_PY_PATH, "utf-8");
    const pythonGates: Record<string, number> = {};
    for (const line of py.split("\n")) {
      const match = /^([A-Z][A-Z0-9_]*)\s*:\s*(?:int|float)\s*=\s*([-\d.]+)/.exec(line.trim());
      if (match) pythonGates[match[1]] = Number(match[2]);
    }
    expect(Object.keys(pythonGates).sort()).toEqual(Object.keys(EXPECTED_GATES).sort());
    for (const [key, value] of Object.entries(pythonGates)) {
      expect(value, `twr_gates.py::${key}`).toBe(mod.TWR_GATES[key]);
    }
  });

  it("no longer exports the 252/N annualizer that produced +3,288,954.62%", () => {
    // Defect #4's TypeScript twin. Annualization is Act/365 over calendar_days
    // and is computed once, in scripts/lib/twr_math.py; the payload ships it as
    // a GatedValue. The web layer only gates and renders.
    expect((mod as Record<string, unknown>).annualizedTwr).toBeUndefined();
    expect(PERFORMANCE_TWR_SOURCE).not.toContain("TRADING_DAYS / Math.max");
  });
});

// ---------------------------------------------------------------------------
// §A.3 — one predicate per metric family, all reading the table
// ---------------------------------------------------------------------------

describe("gate predicates", () => {
  it("dispersion (volatility, drawdown, VaR, CVaR) gates at N=20, not 60", () => {
    // Deliberate threshold change: k = floor(0.05N) >= 1 requires N >= 20.
    expect(mod.isDispersionGated(19)).toBe(true);
    expect(mod.isDispersionGated(20)).toBe(false);
    expect(mod.isDispersionGated(60)).toBe(false);
  });

  it("ratios (sharpe, sortino) gate at N=60, not 20", () => {
    // SE of an annualized Sharpe is ~sqrt(252/N); at N=20 that is +/-3.5.
    expect(mod.isRatioGated(59)).toBe(true);
    expect(mod.isRatioGated(60)).toBe(false);
  });

  it("MWR gates at N=20", () => {
    expect(mod.isMwrGated(19)).toBe(true);
    expect(mod.isMwrGated(20)).toBe(false);
    // The live card said "needs 20 sessions (N=57)" at N=57. 57 >= 20 passes.
    expect(mod.isMwrGated(57)).toBe(false);
  });

  it("benchmark gates on the ALIGNED sample and on coverage, not on n_returns", () => {
    // n_common 39 < MIN_N_BENCHMARK 40
    expect(mod.isBenchmarkGated(39, 39)).toBe(true);
    expect(mod.isBenchmarkGated(40, 40)).toBe(false);
    // coverage = 50/60 = 0.8333 < BENCHMARK_MIN_COVERAGE 0.90
    expect(mod.isBenchmarkGated(50, 60)).toBe(true);
    // coverage = 54/60 = 0.90 exactly -> passes
    expect(mod.isBenchmarkGated(54, 60)).toBe(false);
  });

  it("annualization gates on calendar days, not on session count", () => {
    // GIPS: never annualize a sub-year period. 79 days is the live case.
    expect(mod.isAnnualizationGated(79)).toBe(true);
    expect(mod.isAnnualizationGated(364)).toBe(true);
    expect(mod.isAnnualizationGated(365)).toBe(false);
  });

  it("low-confidence flags are advisory, not gates", () => {
    expect(mod.isLowConfidenceVar(99)).toBe(true);
    expect(mod.isLowConfidenceVar(100)).toBe(false);
    expect(mod.isLowConfidenceRatio(251)).toBe(true);
    expect(mod.isLowConfidenceRatio(252)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// §C.3 — plausibility guards
// ---------------------------------------------------------------------------

describe("plausibility guards", () => {
  it("flags an annualized magnitude above 1000%/yr", () => {
    expect(mod.isImplausibleAnnualized(0.48)).toBe(false);
    expect(mod.isImplausibleAnnualized(10)).toBe(false);
    expect(mod.isImplausibleAnnualized(10.0001)).toBe(true);
    // The live render: +3,288,954.62%
    expect(mod.isImplausibleAnnualized(32889.5462)).toBe(true);
    expect(mod.isImplausibleAnnualized(-32889.5462)).toBe(true);
  });

  it("flags an alpha magnitude above 100%/yr", () => {
    expect(mod.isImplausibleAlpha(0.41)).toBe(false);
    expect(mod.isImplausibleAlpha(1)).toBe(false);
    // The live render: ALPHA +2190.09%
    expect(mod.isImplausibleAlpha(21.9009)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// §A.5 / §D.4 — gateCopy, the ONLY producer of unavailable-metric copy
// ---------------------------------------------------------------------------

describe("gateCopy", () => {
  it("renders the N gate from the GatedValue, never from a hardcoded literal", () => {
    expect(mod.gateCopy(gate(19, 20, "insufficient_n"), "MWR")).toBe("needs 20 sessions (N=19)");
    expect(mod.gateCopy(gate(39, 40, "insufficient_n"), "Beta")).toBe("needs 40 sessions (N=39)");
    expect(mod.gateCopy(gate(59, 60, "insufficient_n"), "Sharpe")).toBe("needs 60 sessions (N=59)");
  });

  it("renders every non-N reason with its own copy", () => {
    expect(mod.gateCopy(gate(84, 365, "period_lt_1y"), "Annualized")).toBe("needs 1 year of history");
    expect(mod.gateCopy(gate(60, 60, "no_downside"), "Sortino")).toBe("no losing sessions yet");
    expect(mod.gateCopy(gate(0, 40, "benchmark_unavailable"), "Beta")).toBe("SPY series unavailable");
    expect(mod.gateCopy(gate(57, 40, "benchmark_degenerate"), "Beta")).toBe("SPY series has no variance");
    expect(mod.gateCopy(gate(56, 20, "not_computed"), "Sortino")).toBe("Sortino not computed");
    expect(mod.gateCopy(gate(57, 20, "no_flow_data"), "MWR")).toBe("external flows unavailable");
    expect(mod.gateCopy(gate(60, 20, "no_convergence"), "MWR IRR")).toBe("did not converge");
    expect(mod.gateCopy(gate(60, 20, "no_sign_change"), "MWR IRR")).toBe("no net capital at risk");
    expect(mod.gateCopy(gate(60, 20, "degenerate"), "MWR IRR")).toBe("no cash flows");
    expect(mod.gateCopy(gate(1, 20, "insufficient_dates"), "MWR IRR")).toBe("needs 2 dated cash flows");
    expect(mod.gateCopy(gate(60, 20, "total_loss"), "MWR IRR")).toBe("-100%");
  });

  it("renders benchmark coverage as a percentage of the portfolio sample", () => {
    // For "benchmark_coverage" the GatedValue carries n = n_common and
    // min_n = n_returns (the coverage denominator): 50/60 = 0.8333 -> 83%.
    expect(mod.gateCopy(gate(50, 60, "benchmark_coverage"), "Beta")).toBe("SPY covers 83% of sessions");
    // 30/40 = 0.75 -> 75%
    expect(mod.gateCopy(gate(30, 40, "benchmark_coverage"), "Beta")).toBe("SPY covers 75% of sessions");
  });

  it("prints TRUE coverage, not n_common / MIN_N_BENCHMARK (tests-D6)", () => {
    // The producer knows n_common = 50 and gates it against MIN_N_BENCHMARK = 40,
    // so the GatedValue it builds is { n: 50, min_n: 40 }. Dividing those gives
    // 50/40 = 125%, which is not a coverage figure at all — coverage is the
    // ALIGNED sample over the PORTFOLIO sample: 50 / 60 = 0.8333... -> 83%.
    const coverageGate = {
      value: null,
      n: 50,
      min_n: mod.TWR_GATES.MIN_N_BENCHMARK,
      unavailable_reason: "benchmark_coverage",
      coverage: 50 / 60,
    } as unknown as GatedValue;

    expect(mod.gateCopy(coverageGate, "Beta")).toBe("SPY covers 83% of sessions");
    expect(mod.gateCopy(coverageGate, "Beta")).not.toContain("125%");
  });

  it("returns empty copy for a value that is present", () => {
    expect(mod.gateCopy(gate(60, 20, null, 0.05020769210515868), "MWR")).toBe("");
  });

  it("never renders an N-gate string for a non-N reason (F6)", () => {
    // The live defect: "MWR IRR -- needs 20 sessions (N=57)" for a metric that
    // was never computed. 57 >= 20; the sample size was never the problem.
    const nonNReasons = [
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
    ];
    for (const reason of nonNReasons) {
      const copy = mod.gateCopy(gate(57, 20, reason), "MWR");
      expect(copy, reason).not.toContain("sessions (N=");
      expect(copy.length, reason).toBeGreaterThan(0);
    }
  });

  it("produces non-empty copy for every reason in the union (exhaustiveness)", () => {
    const allReasons = [
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
    ];
    for (const reason of allReasons) {
      expect(mod.gateCopy(gate(30, 40, reason), "Metric").trim().length, reason).toBeGreaterThan(0);
    }
  });
});
