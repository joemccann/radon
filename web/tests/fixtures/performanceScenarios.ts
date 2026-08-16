/**
 * Shared v2 performance payload fixtures.
 *
 * Contract source: docs Performance / TWR Refactor Specification §C.5 (payload
 * v2), §E.6 (golden 60-session fixture), §E.8 (production regression fixtures).
 *
 * Every literal below is either (a) copied verbatim from the confirmed live
 * production payload (Turso performance_snapshots, taken_at
 * 2026-08-15T14:55:40Z) or (b) hand-computed with the arithmetic written in a
 * comment directly above it. No value is derived at runtime.
 */

export type GatedValueFixture = {
  value: number | null;
  n: number;
  min_n: number;
  unavailable_reason: string | null;
  low_confidence?: boolean;
};

export type WarningFixture = {
  code: string;
  severity: "info" | "warn" | "error";
  message: string;
  context: Record<string, unknown>;
};

export type BenchmarkBlockFixture = {
  symbol: string;
  n_common: number;
  coverage: number;
  benchmark_return: number;
  beta: number;
  alpha_annualized: number;
  correlation: number;
  r_squared: number;
  tracking_error: number;
  information_ratio: number;
  basis: string;
  low_confidence: boolean;
};

export type SeriesPointFixture = {
  date: string;
  nav: number;
  twr_index: number;
  daily_return: number | null;
  cum_return: number;
  drawdown: number;
  flow: number;
  skipped: boolean;
  benchmark_close?: number | null;
  benchmark_return?: number | null;
};

export type SubperiodFixture = {
  date: string;
  b: number;
  e: number;
  c: number;
  denominator: number;
  r: number | null;
  cum_r: number | null;
  gap_days: number;
  flags: string[];
  skip_reason: string | null;
};

export type PerformancePayloadV2 = {
  schema_version: 2;
  status: "ok" | "stale" | "degraded" | "insufficient_data" | "unavailable";
  generated_at: string;
  account_id: string;
  methodology: Record<string, unknown>;
  nav_source: "flex_live" | "disk_cache" | "turso" | "none";
  nav_as_of: string;
  nav_sessions_behind: number;
  flows_status: "ok" | "empty_verified" | "failed";
  flows_source: string;
  period_start: string;
  period_end: string;
  calendar_days: number;
  counts: {
    n_nav_observations: number;
    n_subperiods: number;
    n_returns: number;
    n_skipped: number;
    n_suspect: number;
  };
  /**
   * Nullable on purpose: `_suppressed_payload` in scripts/perf_twr_builder.py
   * emits `twr: null` outright on every degraded branch (verified by running
   * the builder), not a twr object whose cum_return happens to be null.
   */
  twr: {
    cum_return: number | null;
    annualized: GatedValueFixture;
    excludes_suspect: boolean;
  } | null;
  mwr: {
    period_return: GatedValueFixture;
    annualized: GatedValueFixture;
    multiple_sign_changes: boolean;
  };
  risk: Record<string, GatedValueFixture>;
  drawdown_detail: Record<string, unknown>;
  distribution: Record<string, GatedValueFixture>;
  benchmark: BenchmarkBlockFixture | null;
  /**
   * Optional per field on purpose: `_suppressed_payload` in
   * scripts/perf_twr_builder.py emits `equity: {}` on every degraded branch, so
   * a fixture claiming to be builder output must be able to say so.
   */
  equity: {
    starting?: number | null;
    ending?: number | null;
    net_external_flows?: number | null;
    investment_pnl?: number | null;
  };
  series: SeriesPointFixture[];
  subperiods: SubperiodFixture[];
  warnings: WarningFixture[];
};

function gated(
  value: number | null,
  n: number,
  min_n: number,
  unavailable_reason: string | null,
  low_confidence = false,
): GatedValueFixture {
  return { value, n, min_n, unavailable_reason, low_confidence };
}

/** Every gated slot present-and-null, so a fixture can override only what it pins. */
function suppressedGates(n: number, reason: string): Record<string, GatedValueFixture> {
  return {
    volatility: gated(null, n, 20, reason),
    sharpe_ratio: gated(null, n, 60, reason),
    sortino_ratio: gated(null, n, 60, reason),
    // scripts/perf_twr_builder.py `_RISK_MIN_N` puts downside_deviation on the
    // dispersion floor (20), not the ratio floor.
    downside_deviation: gated(null, n, 20, reason),
    max_drawdown: gated(null, n, 20, reason),
    current_drawdown: gated(null, n, 20, reason),
    var_95: gated(null, n, 20, reason),
    cvar_95: gated(null, n, 20, reason),
  };
}

function emptyDistribution(n: number, reason: string): Record<string, GatedValueFixture> {
  return {
    hit_rate: gated(null, n, 20, reason),
    best_day: gated(null, n, 20, reason),
    worst_day: gated(null, n, 20, reason),
    average_up_day: gated(null, n, 20, reason),
    average_down_day: gated(null, n, 20, reason),
    win_loss_ratio: gated(null, n, 20, reason),
    positive_days: gated(null, n, 20, reason),
    negative_days: gated(null, n, 20, reason),
    flat_days: gated(null, n, 20, reason),
  };
}

const METHODOLOGY_OK = {
  curve_type: "twr_daily_eod",
  return_basis: "time_weighted",
  flow_convention: "eod",
  day_count: "act/365",
  vol_scaling_days: 252,
  sortino_target: 0.0,
  risk_free_rate: 0.02,
  risk_free_source: "fred_dgs3mo",
  benchmark_basis: "price_return",
  inferred_flows: [] as unknown[],
};

/**
 * §E.6 golden path — 61 NAV observations, 60 returns, 2026-01-02 .. 2026-03-27.
 *
 * Pinned by the spec (all hand-derivable from the 6-value return pattern
 * [+0.006,-0.004,+0.002,-0.008,+0.010,-0.001] repeated 10x):
 *   cum_return   = (1.006*0.996*1.002*0.992*1.010*0.999)^10 - 1 = 0.050112307160331326
 *   calendar_days= (2026-03-27 - 2026-01-02).days = 84  -> annualized gated period_lt_1y
 *   net flows    = +100000.00 (02-13) + -25000.00 (03-06) = 75000.00
 *   investment_pnl = 182217.35574011656 - 100000.00 - 75000.00 = 7217.35574011656
 *   mwr.period_return (Modified Dietz) = 0.05020769210515868
 * Series here is a 3-point excerpt (first / flow day / last) — enough for the
 * chart + card render contract; the full 61-point series lives in the Python
 * fixture tests/fixtures/perf/golden_60.json.
 */
export function goldenOkPayload(): PerformancePayloadV2 {
  return {
    schema_version: 2,
    status: "ok",
    generated_at: "2026-03-27T21:05:00.000000Z",
    account_id: "U0000000",
    methodology: { ...METHODOLOGY_OK },
    nav_source: "flex_live",
    nav_as_of: "2026-03-27",
    nav_sessions_behind: 0,
    flows_status: "ok",
    flows_source: "flex_cash_transactions+transfers",
    period_start: "2026-01-02",
    period_end: "2026-03-27",
    calendar_days: 84,
    counts: {
      n_nav_observations: 61,
      n_subperiods: 60,
      n_returns: 60,
      n_skipped: 0,
      n_suspect: 0,
    },
    twr: {
      cum_return: 0.050112307160331326,
      // 84 calendar days < MIN_CALENDAR_DAYS_ANNUALIZED (365)
      annualized: gated(null, 84, 365, "period_lt_1y"),
      excludes_suspect: false,
    },
    mwr: {
      period_return: gated(0.05020769210515868, 60, 20, null, true),
      annualized: gated(null, 84, 365, "period_lt_1y"),
      multiple_sign_changes: false,
    },
    risk: {
      volatility: gated(0.09623593888045874, 60, 20, null, true),
      sharpe_ratio: gated(1.9763572406782934, 60, 60, null, true),
      sortino_ratio: gated(3.2608857445808517, 60, 60, null, true),
      downside_deviation: gated(0.05832666628567074, 60, 60, null, true),
      max_drawdown: gated(-0.009991936000000146, 60, 20, null, true),
      current_drawdown: gated(-0.000999, 60, 20, null, true),
      var_95: gated(-0.008, 60, 20, null, true),
      cvar_95: gated(-0.008, 60, 20, null, true),
    },
    drawdown_detail: {
      trough_date: "2026-01-08",
      peak_date: "2026-01-07",
      trough_days: 1,
      recovery_days: 2,
      ongoing: false,
    },
    distribution: {
      hit_rate: gated(0.5, 60, 20, null, true),
      best_day: gated(0.01, 60, 20, null, true),
      worst_day: gated(-0.008, 60, 20, null, true),
      average_up_day: gated(0.006, 60, 20, null, true),
      average_down_day: gated(-0.004333333333333333, 60, 20, null, true),
      win_loss_ratio: gated(1.0, 60, 20, null, true),
      positive_days: gated(30, 60, 20, null, true),
      negative_days: gated(30, 60, 20, null, true),
      flat_days: gated(0, 60, 20, null, true),
    },
    benchmark: {
      symbol: "SPY",
      n_common: 60,
      coverage: 1.0,
      benchmark_return: 0.0312,
      beta: 0.84,
      alpha_annualized: 0.041,
      correlation: 0.71,
      r_squared: 0.5041,
      tracking_error: 0.062,
      information_ratio: 0.33,
      basis: "price_return",
      low_confidence: true,
    },
    equity: {
      starting: 100000.0,
      ending: 182217.35574011656,
      net_external_flows: 75000.0,
      // 182217.35574011656 - 100000.00 - 75000.00
      investment_pnl: 7217.35574011656,
    },
    series: [
      {
        date: "2026-01-02",
        nav: 100000.0,
        twr_index: 100.0,
        daily_return: null,
        cum_return: 0.0,
        drawdown: 0.0,
        flow: 0.0,
        skipped: false,
        benchmark_close: 592.11,
        benchmark_return: null,
      },
      {
        date: "2026-02-13",
        nav: 202474.98754136695,
        twr_index: 100.6,
        daily_return: 0.006,
        cum_return: 0.006,
        drawdown: 0.0,
        flow: 100000.0,
        skipped: false,
        benchmark_close: 601.4,
        benchmark_return: 0.004,
      },
      {
        date: "2026-03-27",
        nav: 182217.35574011656,
        // twr_index = 100 * (1 + 0.050112307160331326)
        twr_index: 105.01123071603313,
        daily_return: -0.001,
        cum_return: 0.050112307160331326,
        drawdown: -0.000999,
        flow: 0.0,
        skipped: false,
        benchmark_close: 610.58,
        benchmark_return: -0.002,
      },
    ],
    subperiods: [
      {
        date: "2026-02-13",
        b: 102577.5651064734,
        e: 202474.98754136695,
        c: 100000.0,
        denominator: 102577.5651064734,
        // (202474.98754136695 - 100000 - 102577.5651064734) / 102577.5651064734
        r: -0.001,
        cum_r: 0.006,
        gap_days: 1,
        flags: ["flow_dominant"],
        skip_reason: null,
      },
    ],
    warnings: [],
  };
}

/**
 * §E.8 fixture 77 — the Flex flows fetch raised. TWR must not be published.
 * This is the exact condition the live payload violated (it published
 * status "ok" with warnings []).
 *
 * R8: the previous revision of this fixture carried a comment claiming its
 * literals were "transcribed from the actual `_suppressed_payload` output".
 * They were not — it claimed `calendar_days 0`, `equity {}`, `counts.n_returns
 * 0` and `risk.*.n 0`. Every literal below IS the real builder output, captured
 * by running:
 *
 *   build_payload([NavObservation(d, v) for d, v in sorted(golden_nav().items())],
 *                 FlowSet.failed("timeout"),
 *                 nav_source="flex_live", nav_sessions_behind=0,
 *                 risk_free_rate=0.02, risk_free_source="fred_dgs3mo",
 *                 account_id="U0000000")
 *
 * which emits:
 *
 *   twr           null                (not an object with a null cum_return)
 *   calendar_days 84                  (2026-03-27 - 2026-01-02).days
 *   counts        n_nav_observations 61, n_subperiods 60, n_returns 60
 *   equity        {starting 100000.0, ending 182217.35574011656,
 *                  net_external_flows null, investment_pnl null}
 *   risk/mwr/dist every gate n=60, reason "no_flow_data"
 *   series []     subperiods []
 *
 * `generated_at` is the only substituted value (the builder stamps the wall
 * clock); everything else is verbatim.
 *
 * ANTI-PIN, now discharged: `counts.n_returns` used to read 60 on a branch
 * that chained ZERO returns and ships `series: []` / `subperiods: []`, and the
 * panel rendered it as "Ending equity $182,217.36 / 2026-03-27 / N=60". R8
 * stopped the builder emitting it, so this literal is 0 and the hero subtitle
 * reads N=--. The GatedValue `n` stays 60 on purpose (§C.5): that is the sample
 * the NAV series supports, so no card blames its sample size for a flows
 * failure. Two different numbers, two different questions.
 */
export function flowsFailedDegradedPayload(): PerformancePayloadV2 {
  return {
    schema_version: 2,
    status: "degraded",
    generated_at: "2026-03-27T21:05:00.000000Z",
    account_id: "U0000000",
    methodology: { ...METHODOLOGY_OK },
    nav_source: "flex_live",
    nav_as_of: "2026-03-27",
    nav_sessions_behind: 0,
    flows_status: "failed",
    flows_source: "",
    period_start: "2026-01-02",
    period_end: "2026-03-27",
    calendar_days: 84,
    counts: {
      n_nav_observations: 61,
      n_subperiods: 60,
      n_returns: 0,
      n_skipped: 0,
      n_suspect: 0,
    },
    twr: null,
    mwr: {
      // N=60 passes MIN_N_MWR=20; the value is null for a NON-N reason.
      period_return: gated(null, 60, 20, "no_flow_data"),
      annualized: gated(null, 60, 20, "no_flow_data"),
      multiple_sign_changes: false,
    },
    risk: suppressedGates(60, "no_flow_data"),
    drawdown_detail: {
      trough_date: null,
      peak_date: null,
      trough_days: null,
      recovery_days: null,
      ongoing: false,
    },
    distribution: emptyDistribution(60, "no_flow_data"),
    benchmark: null,
    equity: {
      starting: 100000.0,
      ending: 182217.35574011656,
      net_external_flows: null,
      investment_pnl: null,
    },
    series: [],
    subperiods: [],
    warnings: [
      {
        code: "FLOWS_FETCH_FAILED",
        severity: "error",
        message: "External flows could not be fetched (timeout); TWR is suppressed.",
        context: { reason: "timeout" },
      },
    ],
  };
}

/**
 * §C.2 Gate 2, live regression — nav_source disk_cache, period_end 2026-03-20
 * while the run date is 2026-08-15. sessions_behind 105 > 2.
 */
export function staleDiskCachePayload(): PerformancePayloadV2 {
  const base = goldenOkPayload();
  return {
    ...base,
    status: "stale",
    generated_at: "2026-08-15T14:55:40.320113Z",
    nav_source: "disk_cache",
    nav_as_of: "2026-03-20",
    nav_sessions_behind: 105,
    period_start: "2025-12-31",
    period_end: "2026-03-20",
    calendar_days: 79,
    warnings: [
      {
        code: "NAV_SOURCE_DISK",
        severity: "warn",
        message: "NAV served from the on-disk cache, not a live Flex fetch.",
        context: { nav_source: "disk_cache" },
      },
      {
        code: "NAV_STALE",
        severity: "warn",
        message: "NAV is 105 sessions behind the last completed session.",
        context: { nav_as_of: "2026-03-20", sessions_behind: 105 },
      },
    ],
  };
}

/** §C.1 / §E.1 case 13 — one NAV observation. All v2 keys still present. */
export function insufficientDataPayload(): PerformancePayloadV2 {
  const base = goldenOkPayload();
  return {
    ...base,
    status: "insufficient_data",
    counts: { n_nav_observations: 1, n_subperiods: 0, n_returns: 0, n_skipped: 0, n_suspect: 0 },
    calendar_days: 0,
    period_start: "2026-08-14",
    period_end: "2026-08-14",
    twr: { cum_return: null, annualized: gated(null, 0, 365, "insufficient_n"), excludes_suspect: false },
    mwr: {
      period_return: gated(null, 0, 20, "insufficient_n"),
      annualized: gated(null, 0, 20, "insufficient_n"),
      multiple_sign_changes: false,
    },
    risk: suppressedGates(0, "insufficient_n"),
    distribution: emptyDistribution(0, "insufficient_n"),
    benchmark: null,
    series: [base.series[0]],
    subperiods: [],
    warnings: [
      {
        code: "NAV_GAP",
        severity: "info",
        message: "Needs at least 2 NAV observations (have 1).",
        context: { n_nav_observations: 1 },
      },
    ],
  };
}

/** §C.1 / §E.1 case 14 — no NAV series at all from Flex, disk or Turso. */
export function unavailablePayload(): PerformancePayloadV2 {
  const base = insufficientDataPayload();
  return {
    ...base,
    status: "unavailable",
    nav_source: "none",
    nav_as_of: "",
    nav_sessions_behind: 0,
    counts: { n_nav_observations: 0, n_subperiods: 0, n_returns: 0, n_skipped: 0, n_suspect: 0 },
    series: [],
    warnings: [
      {
        code: "NAV_UNAVAILABLE",
        severity: "error",
        message: "No NAV series available (Flex, disk and Turso all empty).",
        context: {},
      },
    ],
  };
}

/**
 * §E.8 fixture 74 / §C.3 — a quarantined suspect subperiod. The 2026-02-06
 * NAV jump 246713.50 -> 972215.53 with C=0.
 *   inferred flow amount = 972215.53 - 246713.50 = 725502.03
 *   broken (published) return = (972215.53 - 246713.50) / 246713.50 = 2.940666...
 */
export function suspectSubperiodDegradedPayload(): PerformancePayloadV2 {
  const base = goldenOkPayload();
  return {
    ...base,
    status: "degraded",
    twr: { cum_return: null, annualized: gated(null, 79, 365, "period_lt_1y"), excludes_suspect: true },
    counts: { n_nav_observations: 58, n_subperiods: 57, n_returns: 56, n_skipped: 1, n_suspect: 1 },
    period_start: "2025-12-31",
    period_end: "2026-03-20",
    calendar_days: 79,
    benchmark: null,
    risk: suppressedGates(56, "not_computed"),
    distribution: emptyDistribution(56, "not_computed"),
    subperiods: [
      {
        date: "2026-02-06",
        b: 246713.5,
        e: 972215.53,
        c: 0.0,
        denominator: 246713.5,
        r: null,
        cum_r: null,
        gap_days: 1,
        flags: ["suspect"],
        skip_reason: "suspect_no_flow",
      },
    ],
    warnings: [
      {
        code: "SUBPERIOD_SUSPECT",
        severity: "error",
        message: "2026-02-06 moved +294.07% with no recorded external flow. Subperiod quarantined.",
        context: { date: "2026-02-06", begin_nav: 246713.5, end_nav: 972215.53 },
      },
      {
        code: "INFERRED_FLOW_CANDIDATE",
        severity: "info",
        message: "Inferred external flow candidate +725502.03 on 2026-02-06.",
        context: { date: "2026-02-06", amount: 725502.03 },
      },
    ],
  };
}

/**
 * §C.2 Gate 3 / §E.4 case 55 — SPY series present but with zero variance, so
 * NO benchmark-derived field may appear anywhere in the payload.
 */
export function benchmarkUnavailablePayload(): PerformancePayloadV2 {
  const base = goldenOkPayload();
  return {
    ...base,
    benchmark: null,
    series: base.series.map(({ benchmark_close: _c, benchmark_return: _r, ...rest }) => rest),
    warnings: [
      {
        code: "BENCHMARK_UNAVAILABLE",
        severity: "info",
        message: "SPY series has no variance over the aligned window.",
        context: { reason: "benchmark_degenerate" },
      },
    ],
  };
}

/**
 * R9 — the three benchmark suppressions as the PYTHON BUILDER emits them.
 *
 * `build_benchmark_block` returns `(None, reason)` and
 * `scripts/perf_twr_builder.py:1076-1087` turns that into `benchmark: null`
 * plus one BENCHMARK_UNAVAILABLE info warning whose context carries the reason.
 * The reason therefore never reaches the payload's `benchmark` slot, and a
 * reader that maps `benchmark == null` to "no series at all" deletes the beta /
 * alpha / tracking-error cards and with them every benchmark gate string.
 *
 * The reader must lift the reason (and the sample it was measured on) out of
 * the warning context. `n_common` / `n_returns` / `coverage` are REQUIRED in
 * that context — `gateCopy` cannot word "SPY covers 83% of sessions" or
 * "needs 40 sessions (N=30)" without them, so the builder must add them
 * alongside `reason`.
 */
function benchmarkSuppressed(
  reason: string,
  context: Record<string, unknown>,
  message: string,
): PerformancePayloadV2 {
  const base = goldenOkPayload();
  return {
    ...base,
    benchmark: null,
    series: base.series.map(({ benchmark_close: _c, benchmark_return: _r, ...rest }) => rest),
    warnings: [
      {
        code: "BENCHMARK_UNAVAILABLE",
        severity: "info",
        message,
        context: { reason, ...context },
      },
    ],
  };
}

/**
 * n_common 30 aligned sessions < MIN_N_BENCHMARK 40 — the first gate to fail,
 * exactly as `build_benchmark_block` orders them.
 *   gateCopy -> `needs ${min_n} sessions (N=${n})` = "needs 40 sessions (N=30)"
 */
export function benchmarkInsufficientNPayload(): PerformancePayloadV2 {
  return benchmarkSuppressed(
    "insufficient_n",
    // 30 / 60 = 0.5
    { n_common: 30, n_returns: 60, coverage: 0.5 },
    "SPY statistics suppressed: insufficient_n.",
  );
}

/**
 * n_common 50 of 60 portfolio sessions.
 *   coverage = 50 / 60 = 0.8333333333333334 < BENCHMARK_MIN_COVERAGE 0.90
 *   gateCopy -> `SPY covers ${round(0.8333333333333334 * 100)}% of sessions`
 *            =  "SPY covers 83% of sessions"
 */
export function benchmarkCoverageSuppressedPayload(): PerformancePayloadV2 {
  return benchmarkSuppressed(
    "benchmark_coverage",
    { n_common: 50, n_returns: 60, coverage: 0.8333333333333334 },
    "SPY statistics suppressed: benchmark_coverage.",
  );
}

/**
 * stdev(benchmark, ddof=1) == 0 over the aligned window — the gate that kills
 * the live beta 23.93 / alpha +2190.09% block.
 *   gateCopy -> "SPY series has no variance"
 */
export function benchmarkDegenerateSuppressedPayload(): PerformancePayloadV2 {
  return benchmarkSuppressed(
    "benchmark_degenerate",
    { n_common: 60, n_returns: 60, coverage: 1.0 },
    "SPY statistics suppressed: benchmark_degenerate.",
  );
}

/**
 * The confirmed live production payload, transcribed into v2 shape as an
 * ANTI-PIN. No implementation may render these numbers.
 *   cum_return 9.5128           -> +951.28% on screen
 *   annualized 32889.5462       -> +3,288,954.62% on screen
 *   beta 23.93 / alpha 21.9009  -> regressed against an unusable SPY series
 * Real values from Turso performance_snapshots, taken_at 2026-08-15T14:55:40Z.
 */
export const LIVE_DEFECT_VALUES = {
  cumReturn: 9.5128,
  annualized: 32889.5462,
  beta: 23.93,
  alphaAnnualized: 21.9009,
  trackingError: 6.3425,
  sharpe: 2.9,
  maxDrawdown: -0.1961,
  nReturns: 57,
  startingEquity: 99492.93647617,
  endingEquity: 1045949.48105117,
  suspectSubperiodReturn: 2.940666,
  // (972215.53 - 725000 - 246713.50) / 246713.50 — the correct EOD return
  correctedSubperiodReturn: 0.0020348704063621486,
} as const;

/**
 * flows-D4 — the LITERAL live v1 row that /performance serves today.
 *
 * Written by scripts/portfolio_performance.py (which POST /performance still
 * runs; scripts/perf_twr_builder.py has no caller), mirrored to Turso
 * performance_snapshots at taken_at 2026-08-15T14:55:40Z and read back by
 * web/lib/performanceData.ts. It carries:
 *
 *   NO "schema_version"      -> the read path takes the v1 branch
 *   NO "status"              -> defaulted to "ok"      (performanceData.ts:543)
 *   NO "flows_status"        -> defaulted to ""        (performanceData.ts:545)
 *   NO "nav_sessions_behind" -> defaulted to 0         (performanceData.ts:613)
 *
 * so a 2026-03-20 disk cache read on 2026-08-15 self-reports as fresh and
 * healthy. Numbers below are the production values:
 *   total_return   9.5128       -> +951.28% hero
 *   annualized  32889.5462      -> +3,288,954.62%
 *   pnl = 1045949.48105117 - 99492.93647617 = 946456.544575
 *         (a raw NAV delta with the 725,502.03 ACATS inside it; NOT P&L)
 *
 * A payload with no "status" key is LEGACY and must be refused, not defaulted
 * to healthy.
 */
export function legacyV1LivePayload(): Record<string, unknown> {
  return {
    as_of: "2026-03-20",
    last_sync: "2026-08-15",
    period_start: "2025-12-31",
    period_end: "2026-03-20",
    period_label: "Since first NAV",
    benchmark: "SPY",
    benchmark_total_return: 0.0,
    nav_source: "disk_cache",
    trades_source: "ib_nav",
    price_sources: { stocks: "ib_flex_nav", options: "none" },
    methodology: {
      curve_type: "twr_modified_dietz_daily",
      return_basis: "time_weighted",
      risk_free_rate: 0.0412,
      library_strategy: "fred_dgs3mo",
    },
    summary: {
      starting_equity: LIVE_DEFECT_VALUES.startingEquity,
      ending_equity: LIVE_DEFECT_VALUES.endingEquity,
      pnl: 946456.544575,
      trading_days: LIVE_DEFECT_VALUES.nReturns,
      total_return: LIVE_DEFECT_VALUES.cumReturn,
      annualized_return: LIVE_DEFECT_VALUES.annualized,
      sharpe_ratio: LIVE_DEFECT_VALUES.sharpe,
      max_drawdown: LIVE_DEFECT_VALUES.maxDrawdown,
      current_drawdown: 0.0,
      max_drawdown_duration_days: 11,
      beta: LIVE_DEFECT_VALUES.beta,
      alpha: LIVE_DEFECT_VALUES.alphaAnnualized,
      tracking_error: LIVE_DEFECT_VALUES.trackingError,
    },
    metrics: {
      beta: LIVE_DEFECT_VALUES.beta,
      alpha: LIVE_DEFECT_VALUES.alphaAnnualized,
      tracking_error: LIVE_DEFECT_VALUES.trackingError,
    },
    series: [
      { date: "2025-12-31", nav: LIVE_DEFECT_VALUES.startingEquity, return: 0.0, cum_return: 0.0 },
      { date: "2026-02-06", nav: 972215.53, return: LIVE_DEFECT_VALUES.suspectSubperiodReturn, cum_return: 8.4184 },
      {
        date: "2026-03-20",
        nav: LIVE_DEFECT_VALUES.endingEquity,
        return: 0.0,
        cum_return: LIVE_DEFECT_VALUES.cumReturn,
      },
    ],
    warnings: [],
    contracts_missing_history: [],
  };
}

/**
 * flows-D4, second face — the row `/performance` ACTUALLY serves.
 *
 * `performance_snapshots` holds two writers' rows. The date-only row above is
 * the one with no `status`; the row that WINS the `ORDER BY taken_at DESC`
 * read is this one, written by the pre-v2 `perf_twr_builder` at
 * `taken_at 2026-08-15T14:55:40.320113Z`. Verified against Turso.
 *
 * It is more dangerous than the no-status row, because it DECLARES
 * `status: "ok"` with `warnings: []` while carrying:
 *
 *   NO "schema_version"  -> none of the v2 integrity gates ever ran
 *   NO "flows_status"    -> the 725,502.03 ACATS was never looked for
 *   nav_source disk_cache, period_end 2026-03-20, read on 2026-08-15
 *                        -> 105 completed sessions behind, self-reported fresh
 *
 * §C.2 Gate 2 puts `disk_cache` + `> 2` sessions behind at `degraded`. A
 * declared status from a writer that ran no gates is not evidence, so the read
 * path must refuse the whole payload on `schema_version`, not on whether the
 * word "ok" happens to be present.
 */
export function legacyV1DeclaredOkPayload(): Record<string, unknown> {
  return {
    status: "ok",
    warnings: [],
    nav_source: "disk_cache",
    period_start: "2025-12-31",
    period_end: "2026-03-20",
    benchmark: "SPY",
    methodology: {
      curve_type: "twr_modified_dietz_daily",
      return_basis: "time_weighted",
      risk_free_rate: 0.0374,
      library_strategy: "fred_dgs3mo",
    },
    summary: {
      total_return: LIVE_DEFECT_VALUES.cumReturn,
      annualized_return: LIVE_DEFECT_VALUES.annualized,
      trading_days: LIVE_DEFECT_VALUES.nReturns,
      period_start: "2025-12-31",
      period_end: "2026-03-20",
      max_drawdown: LIVE_DEFECT_VALUES.maxDrawdown,
      sharpe_ratio: LIVE_DEFECT_VALUES.sharpe,
      beta: LIVE_DEFECT_VALUES.beta,
      alpha: LIVE_DEFECT_VALUES.alphaAnnualized,
      var_95: null,
      cvar_95: null,
    },
    metrics: {
      beta: LIVE_DEFECT_VALUES.beta,
      alpha: LIVE_DEFECT_VALUES.alphaAnnualized,
      tracking_error: LIVE_DEFECT_VALUES.trackingError,
      total_return: LIVE_DEFECT_VALUES.cumReturn,
      annualized_return: LIVE_DEFECT_VALUES.annualized,
    },
    series: [
      { date: "2025-12-31", nav: LIVE_DEFECT_VALUES.startingEquity, return: 0.0, cum_return: 0.0, equity: 100.0 },
      {
        date: "2026-02-06",
        nav: 972215.53,
        return: LIVE_DEFECT_VALUES.suspectSubperiodReturn,
        cum_return: 8.4184,
        equity: 941.84,
      },
      {
        date: "2026-03-20",
        nav: LIVE_DEFECT_VALUES.endingEquity,
        return: -0.08963287792538323,
        cum_return: LIVE_DEFECT_VALUES.cumReturn,
        equity: 1051.2801391701703,
      },
    ],
    twr_subperiods: [
      { report_date: "2025-12-31", b: LIVE_DEFECT_VALUES.startingEquity, e: LIVE_DEFECT_VALUES.startingEquity, c: 0.0, r: 0.0, cum_r: 0.0 },
      { report_date: "2026-02-06", b: 246713.5, e: 972215.53, c: 0.0, r: LIVE_DEFECT_VALUES.suspectSubperiodReturn, cum_r: 8.4184 },
    ],
  };
}

/**
 * math-D3 — NAV doubles purely from a +100,000 external flow, so the period's
 * own time-weighted return is exactly 0%.
 *
 *   r_1 = (100000 - 0      - 100000) / 100000 = 0.0
 *   r_2 = (200000 - 100000 - 100000) / 100000 = 0.0
 *   cum_return = (1+0)(1+0) - 1 = 0.0   ->  twr_index stays 100.00 throughout
 *
 * Benchmark closes 100.00 -> 110.00 -> 111.00, i.e. +11.00% over the window.
 * Plotting dollar NAV against a dollar-rebased benchmark draws +100% against
 * +11% for a period whose published TWR is zero; §C.5 requires twr_index to be
 * the chart's y-series so both lines share one base.
 */
export function flowDoubledNavZeroTwrPayload(): PerformancePayloadV2 {
  return {
    schema_version: 2,
    status: "ok",
    generated_at: "2026-01-07T21:05:00.000000Z",
    account_id: "U0000000",
    methodology: { ...METHODOLOGY_OK },
    nav_source: "flex_live",
    nav_as_of: "2026-01-07",
    nav_sessions_behind: 0,
    flows_status: "ok",
    flows_source: "flex_cash_transactions+transfers",
    period_start: "2026-01-05",
    period_end: "2026-01-07",
    calendar_days: 2,
    counts: { n_nav_observations: 3, n_subperiods: 2, n_returns: 2, n_skipped: 0, n_suspect: 0 },
    twr: { cum_return: 0.0, annualized: gated(null, 2, 365, "period_lt_1y"), excludes_suspect: false },
    mwr: {
      period_return: gated(null, 2, 20, "insufficient_n"),
      annualized: gated(null, 2, 20, "insufficient_n"),
      multiple_sign_changes: false,
    },
    risk: suppressedGates(2, "insufficient_n"),
    drawdown_detail: {},
    distribution: emptyDistribution(2, "insufficient_n"),
    benchmark: {
      symbol: "SPY",
      n_common: 2,
      coverage: 1.0,
      // 111.00 / 100.00 - 1
      benchmark_return: 0.11,
      beta: 0.0,
      alpha_annualized: 0.0,
      correlation: 0.0,
      r_squared: 0.0,
      tracking_error: 0.0,
      information_ratio: 0.0,
      basis: "price_return",
      low_confidence: true,
    },
    equity: {
      starting: 100000.0,
      ending: 200000.0,
      net_external_flows: 100000.0,
      // 200000 - 100000 - 100000
      investment_pnl: 0.0,
    },
    series: [
      {
        date: "2026-01-05",
        nav: 100000.0,
        twr_index: 100.0,
        daily_return: null,
        cum_return: 0.0,
        drawdown: 0.0,
        flow: 0.0,
        skipped: false,
        benchmark_close: 100.0,
        benchmark_return: null,
      },
      {
        date: "2026-01-06",
        nav: 100000.0,
        twr_index: 100.0,
        daily_return: 0.0,
        cum_return: 0.0,
        drawdown: 0.0,
        flow: 0.0,
        skipped: false,
        benchmark_close: 110.0,
        // 110/100 - 1
        benchmark_return: 0.1,
      },
      {
        date: "2026-01-07",
        nav: 200000.0,
        twr_index: 100.0,
        daily_return: 0.0,
        cum_return: 0.0,
        drawdown: 0.0,
        flow: 100000.0,
        skipped: false,
        benchmark_close: 111.0,
        // 111/110 - 1
        benchmark_return: 0.00909090909090909,
      },
    ],
    subperiods: [
      {
        date: "2026-01-07",
        b: 100000.0,
        e: 200000.0,
        c: 100000.0,
        denominator: 100000.0,
        r: 0.0,
        cum_r: 0.0,
        gap_days: 1,
        // |C|/B = 1.0 > FLOW_DOMINANT_RATIO 0.25
        flags: ["flow_dominant"],
        skip_reason: null,
      },
    ],
    warnings: [],
  };
}

/**
 * tests-D7 / tests-D6 — SPY aligns on only 50 of the portfolio's 60 sessions.
 *   coverage = 50 / 60 = 0.8333333333333334 < BENCHMARK_MIN_COVERAGE 0.90
 * so no benchmark statistic may be published. The REASON must reach the screen:
 * a card rendering "--" plus "SPY covers 83% of sessions", never a silently
 * deleted card.
 */
export function benchmarkCoverageShortfallPayload(): PerformancePayloadV2 {
  const base = goldenOkPayload();
  const benchmark = base.benchmark as BenchmarkBlockFixture;
  return {
    ...base,
    benchmark: {
      ...benchmark,
      n_common: 50,
      // 50 / 60
      coverage: 0.8333333333333334,
    },
  };
}

/** A payload that still carries the live defect magnitudes. Must be guarded. */
export function livePathologicalPayload(): PerformancePayloadV2 {
  const base = staleDiskCachePayload();
  return {
    ...base,
    status: "degraded",
    flows_status: "failed",
    twr: {
      cum_return: LIVE_DEFECT_VALUES.cumReturn,
      annualized: gated(LIVE_DEFECT_VALUES.annualized, 57, 365, null),
      excludes_suspect: false,
    },
    counts: {
      n_nav_observations: 58,
      n_subperiods: 57,
      n_returns: LIVE_DEFECT_VALUES.nReturns,
      n_skipped: 0,
      n_suspect: 0,
    },
    benchmark: {
      symbol: "SPY",
      n_common: 57,
      coverage: 1.0,
      benchmark_return: 0.0,
      beta: LIVE_DEFECT_VALUES.beta,
      alpha_annualized: LIVE_DEFECT_VALUES.alphaAnnualized,
      correlation: 0.02,
      r_squared: 0.0004,
      tracking_error: LIVE_DEFECT_VALUES.trackingError,
      information_ratio: 3.4,
      basis: "price_return",
      low_confidence: true,
    },
    equity: {
      starting: LIVE_DEFECT_VALUES.startingEquity,
      ending: LIVE_DEFECT_VALUES.endingEquity,
      net_external_flows: 0.0,
      investment_pnl: 946456.544575,
    },
  };
}
