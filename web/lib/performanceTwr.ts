/**
 * The single gate table for the performance stack, plus the only producer of
 * unavailable-metric copy.
 *
 * MIRROR OF scripts/lib/twr_gates.py — DO NOT EDIT ONE WITHOUT THE OTHER.
 * tests/test_twr_gate_parity.py fails the build on drift.
 *
 * No arithmetic lives here. Every published number is computed once, in
 * scripts/lib/twr_math.py, and shipped in the payload as a GatedValue. The web
 * layer only decides whether a value clears its gate and how to word the
 * absence.
 */

export const TWR_GATES = {
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
  UNEXPLAINED_OUTLIER_MULTIPLE: 5,
  UNEXPLAINED_TAIL_QUANTILE: 0.95,
  UNEXPLAINED_TAIL_MULTIPLE: 3,
  UNEXPLAINED_ABSOLUTE_FLOOR: 0.1,
  FLOW_DOMINANT_RATIO: 0.25,
  IMPLAUSIBLE_ANNUALIZED: 10,
  IMPLAUSIBLE_ALPHA: 10,
  MAX_SUBPERIOD_GAP_DAYS: 4,
  NAV_STALENESS_BUDGET_SESSIONS: 2,
} as const;

/** Non-numeric half of the table; kept separate so the parity scan stays numeric. */
export const TWR_CONVENTIONS = {
  FLOW_CONVENTION: "bod",
  SORTINO_TARGET: 0,
} as const;

export type GatedValue = {
  value: number | null;
  n: number;
  min_n: number;
  unavailable_reason: string | null;
  low_confidence?: boolean;
  /** Aligned sample over portfolio sample. Only a benchmark gate carries it;
   *  n / min_n is a floor comparison and is not a coverage ratio. */
  coverage?: number;
};

/** Every reason a published number may be absent. gateCopy switches exhaustively. */
export type GateReason =
  | "insufficient_n"
  | "period_lt_1y"
  | "no_downside"
  | "benchmark_unavailable"
  | "benchmark_coverage"
  | "benchmark_degenerate"
  | "not_computed"
  | "no_flow_data"
  | "no_convergence"
  | "no_sign_change"
  | "degenerate"
  | "insufficient_dates"
  | "total_loss"
  | "implausible"
  | "invalid_total_return"
  | "nav_unavailable";

// ---------------------------------------------------------------------------
// Gate predicates — one per metric family, all reading the table
// ---------------------------------------------------------------------------

/** volatility, drawdowns, VaR, CVaR, hit rate, best/worst day. */
export function isDispersionGated(nReturns: number): boolean {
  return nReturns < TWR_GATES.MIN_N_DISPERSION;
}

/** sharpe, sortino. */
export function isRatioGated(nReturns: number): boolean {
  return nReturns < TWR_GATES.MIN_N_RATIO;
}

/** mwr_period and mwr_annualized. */
export function isMwrGated(nReturns: number): boolean {
  return nReturns < TWR_GATES.MIN_N_MWR;
}

/** Beta, alpha, tracking error, information ratio, benchmark return. Gates on
 *  the ALIGNED sample and on its coverage of the portfolio sample. */
export function isBenchmarkGated(nCommon: number, nReturns: number): boolean {
  if (nCommon < TWR_GATES.MIN_N_BENCHMARK) return true;
  if (nReturns > 0 && nCommon / nReturns < TWR_GATES.BENCHMARK_MIN_COVERAGE) return true;
  return false;
}

/** GIPS: never annualize a sub-year period. */
export function isAnnualizationGated(calendarDays: number): boolean {
  return calendarDays < TWR_GATES.MIN_CALENDAR_DAYS_ANNUALIZED;
}

export function isLowConfidenceVar(nReturns: number): boolean {
  return nReturns < TWR_GATES.VAR_LOW_CONFIDENCE_N;
}

export function isLowConfidenceRatio(nReturns: number): boolean {
  return nReturns < TWR_GATES.RATIO_LOW_CONFIDENCE_N;
}

/** An annualized return past 1000%/yr is a data defect, not a result. */
export function isImplausibleAnnualized(annualized: number): boolean {
  return Math.abs(annualized) > TWR_GATES.IMPLAUSIBLE_ANNUALIZED;
}

/** A broad-index regression past 100%/yr alpha is a data defect. */
export function isImplausibleAlpha(alphaAnnualized: number): boolean {
  return Math.abs(alphaAnnualized) > TWR_GATES.IMPLAUSIBLE_ALPHA;
}

/**
 * A total return is implausible when the rate it implies per year is. The
 * annualized card can be gated away by `period_lt_1y` while the total itself
 * still renders, which is how +951.28% over 79 days reached the hero. Judging
 * the total on its own annual equivalent closes that gap without inventing a
 * second threshold.
 */
export function isImplausibleCumReturn(cumReturn: number, calendarDays: number): boolean {
  if (!Number.isFinite(cumReturn)) return true;
  if (cumReturn < -1) return true;
  const years = calendarDays > 0 ? calendarDays / TWR_GATES.DAYS_PER_YEAR : 1;
  return isImplausibleAnnualized((1 + cumReturn) ** (1 / years) - 1);
}

// ---------------------------------------------------------------------------
// gateCopy — the ONLY producer of unavailable-metric copy
// ---------------------------------------------------------------------------

/** True coverage: aligned sessions over portfolio sessions. `min_n` is the
 *  MIN_N_BENCHMARK floor, so n / min_n reads 125% at 50 of 60 aligned. */
function coveragePercent(gate: GatedValue): number {
  if (gate.coverage != null && Number.isFinite(gate.coverage)) return Math.round(gate.coverage * 100);
  if (gate.min_n <= 0) return 0;
  return Math.round((gate.n / gate.min_n) * 100);
}

function unhandledReason(reason: never, label: string): string {
  void reason;
  return `${label} not computed`;
}

/**
 * Copy for a metric that has no value. Returns "" when a value is present, so a
 * card can never pair a number with an excuse. The reason is produced by the
 * same layer that produced the null, which is what makes it structurally
 * impossible to blame the sample size for a metric that was never computed.
 */
export function gateCopy(gate: GatedValue, label: string): string {
  if (gate.value != null) return "";
  const reason = (gate.unavailable_reason ?? "not_computed") as GateReason;
  switch (reason) {
    case "insufficient_n":
      return `needs ${gate.min_n} sessions (N=${gate.n})`;
    case "period_lt_1y":
      return "needs 1 year of history";
    case "no_downside":
      return "no losing sessions yet";
    case "benchmark_unavailable":
      return "SPY series unavailable";
    case "benchmark_coverage":
      return `SPY covers ${coveragePercent(gate)}% of sessions`;
    case "benchmark_degenerate":
      return "SPY series has no variance";
    case "not_computed":
      return `${label} not computed`;
    case "no_flow_data":
      return "external flows unavailable";
    case "no_convergence":
      return "did not converge";
    case "no_sign_change":
      return "no net capital at risk";
    case "degenerate":
      return "no cash flows";
    case "insufficient_dates":
      return "needs 2 dated cash flows";
    case "total_loss":
      return "-100%";
    case "implausible":
      return "value outside a plausible range";
    case "invalid_total_return":
      return "total return below -100%";
    case "nav_unavailable":
      return "no NAV series available";
    default:
      return unhandledReason(reason, label);
  }
}

/** Convenience constructor for a suppressed metric. */
export function suppressed(reason: GateReason, n: number, minN: number): GatedValue {
  return { value: null, n, min_n: minN, unavailable_reason: reason };
}

/** Convenience constructor for a published metric. */
export function published(value: number, n: number, minN: number, lowConfidence = false): GatedValue {
  return { value, n, min_n: minN, unavailable_reason: null, low_confidence: lowConfidence };
}
