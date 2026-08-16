import { lastCompletedSessionDate } from "./marketSession";
import { isUsTradingDay } from "./serviceHealthWindows";
import {
  isAnnualizationGated,
  isBenchmarkGated,
  isDispersionGated,
  isImplausibleAlpha,
  isImplausibleAnnualized,
  isImplausibleCumReturn,
  isMwrGated,
  isRatioGated,
  published,
  suppressed,
  TWR_GATES,
  type GateReason,
  type GatedValue,
} from "./performanceTwr";
import type {
  PerformanceBenchmarkBlock,
  PerformanceData,
  PerformanceSeriesPoint,
  PerformanceStatus,
  PerformanceSummary,
  PerformanceWarning,
} from "./types";

type RawPoint = {
  date?: unknown;
  equity?: unknown;
  nav?: unknown;
  twr_index?: unknown;
  cum_return?: unknown;
  flow?: unknown;
  skipped?: unknown;
  return?: unknown;
  daily_return?: unknown;
  drawdown?: unknown;
  benchmark_close?: unknown;
  benchmark_return?: unknown;
};

const STATUSES: readonly PerformanceStatus[] = [
  "ok",
  "stale",
  "degraded",
  "insufficient_data",
  "unavailable",
];

/**
 * A snapshot that never declared a status predates the v2 integrity gates: it
 * carries no flow verification, no NAV freshness evidence and no warning list
 * that means anything. Absent is not healthy, so it is refused rather than
 * defaulted. The 2026-08-15 production row (+951.28% under `status: "ok"`) is
 * exactly this shape.
 */
const LEGACY_PAYLOAD_WARNING: PerformanceWarning = {
  code: "LEGACY_PAYLOAD",
  severity: "error",
  message:
    "This snapshot predates the integrity gates. It carries no status, no flow verification and no NAV freshness evidence, so none of its numbers are publishable.",
  context: {},
};

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function declaredStatus(data: Record<string, unknown>): PerformanceStatus | undefined {
  return STATUSES.find((s) => s === data.status);
}

/**
 * Legacy is decided by the schema version, never by whether the payload
 * happens to spell a status. The row production actually serves declares
 * `status: "ok"` while carrying no `schema_version`, so a writer that ran none
 * of the v2 integrity gates gets to name its own health. `schema_version: 2`
 * is the only evidence that the gates ran at all, which is the same test the
 * API applies in `_refuse_legacy_performance_payload`.
 */
function isLegacyPayload(data: Record<string, unknown>): boolean {
  return !isV2Payload(data);
}

/** The one status resolver. A legacy payload degrades; it never defaults to ok
 *  and its own declared status is not consulted. */
function resolveStatus(data: Record<string, unknown>, seriesLength: number): PerformanceStatus {
  if (isLegacyPayload(data)) return seriesLength < 2 ? "insufficient_data" : "degraded";
  return declaredStatus(data) ?? (seriesLength < 2 ? "insufficient_data" : "degraded");
}

/** The one flows_status default, shared by both normalizers and the view, so
 *  they cannot disagree about what an unstated flow fetch means. */
function resolveFlowsStatus(data: Record<string, unknown>): string {
  return nonEmptyString(data.flows_status) ?? (isLegacyPayload(data) ? "failed" : "");
}

function isoDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/**
 * The most recent session whose 16:00 ET close has passed, holidays included.
 *
 * `lastCompletedSessionDate` is weekday-only by design (its own docstring calls
 * holidays an accepted caveat for the scan-storm checks it was written for).
 * DECISION 6 needs the exact Python boundary instead, so a holiday that never
 * opened is walked back off here rather than counted as a session that closed:
 * MLK Monday after the bell resolves to the preceding Friday on both sides.
 */
export function lastCompletedSession(now: Date = new Date()): string {
  let cursor = Date.parse(`${lastCompletedSessionDate(now)}T00:00:00Z`);
  while (!isUsTradingDay(isoDay(cursor))) cursor -= 86_400_000;
  return isoDay(cursor);
}

/**
 * Trading sessions strictly after `navAsOf`, up to AND INCLUDING the last
 * completed session (DECISION 6).
 *
 * The single definition, shared with `scripts.perf_twr_builder.sessions_behind`
 * and pinned on both sides by `tests/fixtures/perf/sessions_behind_parity.json`.
 * Weekends and full-closure US market holidays are not sessions, off the same
 * static table Python reads (`scripts/config/market_holidays.json`) - counting
 * holidays as sessions over-reports staleness by up to four over a summer and
 * silently broke parity with Python everywhere the table has an entry.
 */
function sessionsBehind(navAsOf: string, now: Date = new Date()): number {
  const from = Date.parse(`${navAsOf}T00:00:00Z`);
  const to = Date.parse(`${lastCompletedSession(now)}T00:00:00Z`);
  if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) return 0;
  let sessions = 0;
  for (let day = from + 86_400_000; day <= to; day += 86_400_000) {
    if (isUsTradingDay(isoDay(day))) sessions += 1;
  }
  return sessions;
}

/** Normalization retains what the writer declared, and derives only when the
 *  writer said nothing. Judging the number is the render layer's job. */
function declaredSessionsBehind(data: Record<string, unknown>, navAsOf: string): number {
  return finiteNumber(data.nav_sessions_behind) ?? (navAsOf ? sessionsBehind(navAsOf) : 0);
}

/**
 * Read-time sessions behind: always derived from the NAV date against the
 * current clock, with the writer's own count able to raise it but never lower
 * it.
 *
 * A number frozen into the payload is evidence about the moment it was written,
 * so preferring it lets a March snapshot self-report "0 sessions behind" when
 * it is read in August. The reader owns freshness because only the reader knows
 * what time it is.
 */
function resolveSessionsBehind(data: Record<string, unknown>, navAsOf: string): number {
  const derived = navAsOf ? sessionsBehind(navAsOf) : 0;
  const declared = finiteNumber(data.nav_sessions_behind) ?? 0;
  return Math.max(declared, derived);
}

/** Past the NAV freshness budget, measured at read time. A payload may declare
 *  "ok" and still be stale; the declared status word is not consulted. */
function isBeyondStalenessBudget(navSessionsBehind: number): boolean {
  return navSessionsBehind > TWR_GATES.NAV_STALENESS_BUDGET_SESSIONS;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function tradesSourceFrom(raw: Record<string, unknown>): string {
  return nonEmptyString(raw.trades_source) ?? "ib_nav";
}

function priceSourcesFrom(raw: Record<string, unknown>): PerformanceData["price_sources"] {
  const sources = asRecord(raw.price_sources);
  return {
    stocks: nonEmptyString(sources.stocks) ?? "ib_flex_nav",
    options: nonEmptyString(sources.options) ?? "none",
  };
}

function methodologyFrom(raw: Record<string, unknown>): PerformanceData["methodology"] {
  const methodology = asRecord(raw.methodology);
  return {
    curve_type: nonEmptyString(methodology.curve_type) ?? "twr_modified_dietz_daily",
    return_basis: nonEmptyString(methodology.return_basis) ?? "time_weighted",
    risk_free_rate: finiteNumber(methodology.risk_free_rate) ?? 0,
    library_strategy: nonEmptyString(methodology.library_strategy) ?? "fred_dgs3mo",
    risk_free_source: nonEmptyString(methodology.risk_free_source),
    flow_convention: nonEmptyString(methodology.flow_convention),
    day_count: nonEmptyString(methodology.day_count),
  };
}

// ---------------------------------------------------------------------------
// Series
// ---------------------------------------------------------------------------

function normalizeSeries(raw: unknown): PerformanceSeriesPoint[] {
  if (!Array.isArray(raw)) return [];
  let peak = -Infinity;
  const series: PerformanceSeriesPoint[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const point = item as RawPoint;
    const date = typeof point.date === "string" ? point.date : "";
    const nav = finiteNumber(point.nav);
    const twrIndex = finiteNumber(point.twr_index);
    // §C.5: the TWR index is the plotted series. Dollar NAV moves on deposits,
    // so charting it against a rebased benchmark reads a wire as performance.
    const plotted = twrIndex ?? nav ?? finiteNumber(point.equity);
    if (!date || plotted == null) continue;
    if (plotted > peak) peak = plotted;
    const dailyReturn = finiteNumber(point.daily_return) ?? finiteNumber(point.return) ?? null;
    const drawdown = finiteNumber(point.drawdown) ?? (peak > 0 ? plotted / peak - 1 : 0);
    const normalized: PerformanceSeriesPoint = {
      date,
      equity: plotted,
      daily_return: dailyReturn,
      drawdown,
    };
    if (nav != null) normalized.nav = nav;
    if (twrIndex != null) normalized.twr_index = twrIndex;
    const cumReturn = finiteNumber(point.cum_return);
    if (cumReturn != null) normalized.cum_return = cumReturn;
    const flow = finiteNumber(point.flow);
    if (flow != null) normalized.flow = flow;
    if (typeof point.skipped === "boolean") normalized.skipped = point.skipped;
    const benchmarkClose = finiteNumber(point.benchmark_close);
    if (benchmarkClose != null) normalized.benchmark_close = benchmarkClose;
    const benchmarkReturn = finiteNumber(point.benchmark_return);
    if (benchmarkReturn != null) normalized.benchmark_return = benchmarkReturn;
    series.push(normalized);
  }
  return series;
}

function drawdownDurationDays(series: PerformanceSeriesPoint[]): number {
  let longest = 0;
  let current = 0;
  for (const point of series) {
    if (point.drawdown < 0) {
      current += 1;
      if (current > longest) longest = current;
    } else {
      current = 0;
    }
  }
  return longest;
}

function firstNav(rawSeries: unknown): number | undefined {
  if (!Array.isArray(rawSeries) || rawSeries.length === 0) return undefined;
  return finiteNumber((rawSeries[0] as RawPoint | undefined)?.nav);
}

function lastNav(rawSeries: unknown): number | undefined {
  if (!Array.isArray(rawSeries) || rawSeries.length === 0) return undefined;
  return finiteNumber((rawSeries[rawSeries.length - 1] as RawPoint | undefined)?.nav);
}

// ---------------------------------------------------------------------------
// Warnings
// ---------------------------------------------------------------------------

function isWarningObject(value: unknown): value is PerformanceWarning {
  const record = asRecord(value);
  return typeof record.code === "string" && typeof record.message === "string";
}

function normalizeWarnings(raw: unknown): Array<string | PerformanceWarning> {
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (item): item is string | PerformanceWarning => typeof item === "string" || isWarningObject(item),
  );
}

/** Structured view of the warning list; v1 strings are lifted to LEGACY notes. */
export function performanceWarnings(data: PerformanceData | null | undefined): PerformanceWarning[] {
  const raw = Array.isArray(data?.warnings) ? data.warnings : [];
  return raw.map((warning) =>
    typeof warning === "string"
      ? { code: "LEGACY_NOTE", severity: "warn" as const, message: warning }
      : warning,
  );
}

// ---------------------------------------------------------------------------
// normalizePerformanceData
// ---------------------------------------------------------------------------

function isV2Payload(data: Record<string, unknown>): boolean {
  return finiteNumber(data.schema_version) === 2;
}

function benchmarkBlockFrom(value: unknown): PerformanceBenchmarkBlock | null {
  const record = asRecord(value);
  const symbol = nonEmptyString(record.symbol);
  const benchmarkReturn = finiteNumber(record.benchmark_return);
  const beta = finiteNumber(record.beta);
  if (!symbol || benchmarkReturn == null || beta == null) return null;
  return {
    symbol,
    n_common: finiteNumber(record.n_common) ?? 0,
    coverage: finiteNumber(record.coverage) ?? 0,
    benchmark_return: benchmarkReturn,
    beta,
    alpha_annualized: finiteNumber(record.alpha_annualized) ?? 0,
    correlation: finiteNumber(record.correlation) ?? 0,
    r_squared: finiteNumber(record.r_squared) ?? 0,
    tracking_error: finiteNumber(record.tracking_error) ?? 0,
    information_ratio: finiteNumber(record.information_ratio) ?? 0,
    basis: nonEmptyString(record.basis) ?? "price_return",
    low_confidence: record.low_confidence === true,
  };
}

function gatedRecord(raw: unknown): Record<string, GatedValue> {
  const source = asRecord(raw);
  const out: Record<string, GatedValue> = {};
  for (const [key, value] of Object.entries(source)) {
    const record = asRecord(value);
    out[key] = {
      value: finiteNumber(record.value) ?? null,
      n: finiteNumber(record.n) ?? 0,
      min_n: finiteNumber(record.min_n) ?? 0,
      unavailable_reason: nonEmptyString(record.unavailable_reason) ?? null,
      low_confidence: record.low_confidence === true,
    };
  }
  return out;
}

function gatedValueFrom(raw: unknown, fallbackMinN: number): GatedValue {
  const record = asRecord(raw);
  return {
    value: finiteNumber(record.value) ?? null,
    n: finiteNumber(record.n) ?? 0,
    min_n: finiteNumber(record.min_n) ?? fallbackMinN,
    unavailable_reason: nonEmptyString(record.unavailable_reason) ?? null,
    low_confidence: record.low_confidence === true,
  };
}

function normalizeV2(data: Record<string, unknown>): PerformanceData {
  const series = normalizeSeries(data.series);
  const counts = asRecord(data.counts);
  const equity = asRecord(data.equity);
  const twr = asRecord(data.twr);
  const mwr = asRecord(data.mwr);
  const risk = gatedRecord(data.risk);
  const periodStart = nonEmptyString(data.period_start) ?? series[0]?.date ?? "";
  const periodEnd = nonEmptyString(data.period_end) ?? series[series.length - 1]?.date ?? "";
  const navAsOf = nonEmptyString(data.nav_as_of) ?? periodEnd;
  const generatedAt = nonEmptyString(data.generated_at) ?? "";
  // NaN, not 0: a suppressed branch publishes `equity: {}` and a fabricated
  // zero there reads as a measured zero on the hero.
  const startingEquity = finiteNumber(equity.starting) ?? firstNav(data.series) ?? Number.NaN;
  const endingEquity = finiteNumber(equity.ending) ?? lastNav(data.series) ?? Number.NaN;
  const nReturns = finiteNumber(counts.n_returns) ?? series.length;

  const summary = {
    starting_equity: startingEquity,
    ending_equity: endingEquity,
    pnl: finiteNumber(equity.investment_pnl) ?? Number.NaN,
    trading_days: nReturns,
    total_return: finiteNumber(twr.cum_return) ?? Number.NaN,
    max_drawdown: risk.max_drawdown?.value ?? Number.NaN,
    current_drawdown: risk.current_drawdown?.value ?? Number.NaN,
    max_drawdown_duration_days: drawdownDurationDays(series),
  } as PerformanceSummary;

  return {
    schema_version: 2,
    status: resolveStatus(data, series.length),
    generated_at: generatedAt,
    account_id: nonEmptyString(data.account_id) ?? "",
    nav_source: nonEmptyString(data.nav_source) ?? "none",
    nav_as_of: navAsOf,
    nav_sessions_behind: declaredSessionsBehind(data, navAsOf),
    flows_status: resolveFlowsStatus(data),
    flows_source: nonEmptyString(data.flows_source) ?? "",
    calendar_days: finiteNumber(data.calendar_days) ?? 0,
    counts: {
      n_nav_observations: finiteNumber(counts.n_nav_observations) ?? 0,
      n_subperiods: finiteNumber(counts.n_subperiods) ?? 0,
      n_returns: nReturns,
      n_skipped: finiteNumber(counts.n_skipped) ?? 0,
      n_suspect: finiteNumber(counts.n_suspect) ?? 0,
    },
    twr: {
      cum_return: finiteNumber(twr.cum_return) ?? null,
      annualized: gatedValueFrom(twr.annualized, TWR_GATES.MIN_CALENDAR_DAYS_ANNUALIZED),
      excludes_suspect: twr.excludes_suspect === true,
    },
    mwr: {
      period_return: gatedValueFrom(mwr.period_return, TWR_GATES.MIN_N_MWR),
      annualized: gatedValueFrom(mwr.annualized, TWR_GATES.MIN_N_MWR),
      multiple_sign_changes: mwr.multiple_sign_changes === true,
    },
    risk,
    distribution: gatedRecord(data.distribution),
    drawdown_detail: asRecord(data.drawdown_detail),
    equity: {
      starting: startingEquity,
      ending: endingEquity,
      net_external_flows: finiteNumber(equity.net_external_flows) ?? Number.NaN,
      investment_pnl: finiteNumber(equity.investment_pnl) ?? Number.NaN,
    },
    benchmark: benchmarkBlockFrom(data.benchmark),
    subperiods: Array.isArray(data.subperiods) ? (data.subperiods as Array<Record<string, unknown>>) : [],

    as_of: navAsOf,
    last_sync: generatedAt || navAsOf,
    period_start: periodStart,
    period_end: periodEnd,
    period_label: nonEmptyString(data.period_label) ?? "Since first NAV",
    trades_source: tradesSourceFrom(data),
    price_sources: priceSourcesFrom(data),
    methodology: methodologyFrom(data),
    summary,
    warnings: normalizeWarnings(data.warnings),
    contracts_missing_history: stringList(data.contracts_missing_history),
    series,
  };
}

function normalizeV1(data: Record<string, unknown>): PerformanceData {
  const series = normalizeSeries(data.series);
  const summaryRaw = asRecord(data.summary);
  const metrics = asRecord(data.metrics);
  const startingEquity =
    finiteNumber(summaryRaw.starting_equity) ?? firstNav(data.series) ?? series[0]?.equity ?? 0;
  const endingEquity =
    finiteNumber(summaryRaw.ending_equity) ?? lastNav(data.series) ?? series[series.length - 1]?.equity ?? 0;
  const pnl = finiteNumber(summaryRaw.pnl) ?? endingEquity - startingEquity;
  const tradingDays = finiteNumber(summaryRaw.trading_days) ?? series.length;
  const currentDrawdown =
    finiteNumber(summaryRaw.current_drawdown) ?? series[series.length - 1]?.drawdown ?? 0;
  const maxDrawdownDuration =
    finiteNumber(summaryRaw.max_drawdown_duration_days) ?? drawdownDurationDays(series);
  const periodStart =
    nonEmptyString(data.period_start) ?? nonEmptyString(summaryRaw.period_start) ?? series[0]?.date ?? "";
  const periodEnd =
    nonEmptyString(data.period_end)
    ?? nonEmptyString(summaryRaw.period_end)
    ?? series[series.length - 1]?.date
    ?? "";
  const asOf = nonEmptyString(data.as_of) ?? periodEnd;
  const lastSync = nonEmptyString(data.last_sync) ?? asOf;

  const summary = {
    ...summaryRaw,
    starting_equity: startingEquity,
    ending_equity: endingEquity,
    pnl,
    trading_days: tradingDays,
    total_return: finiteNumber(summaryRaw.total_return) ?? 0,
    annualized_return: finiteNumber(summaryRaw.annualized_return) ?? Number.NaN,
    sharpe_ratio: finiteNumber(summaryRaw.sharpe_ratio) ?? Number.NaN,
    max_drawdown: finiteNumber(summaryRaw.max_drawdown) ?? 0,
    current_drawdown: currentDrawdown,
    max_drawdown_duration_days: maxDrawdownDuration,
    beta: finiteNumber(summaryRaw.beta) ?? finiteNumber(metrics.beta) ?? Number.NaN,
    alpha: finiteNumber(summaryRaw.alpha) ?? finiteNumber(metrics.alpha) ?? Number.NaN,
    tracking_error: finiteNumber(summaryRaw.tracking_error) ?? finiteNumber(metrics.tracking_error) ?? Number.NaN,
    var_95: finiteNumber(summaryRaw.var_95) ?? finiteNumber(metrics.var_95) ?? Number.NaN,
    cvar_95: finiteNumber(summaryRaw.cvar_95) ?? finiteNumber(metrics.cvar_95) ?? Number.NaN,
  } as PerformanceSummary;

  const legacy = isLegacyPayload(data);
  const warnings = normalizeWarnings(data.warnings);

  return {
    as_of: asOf,
    last_sync: lastSync,
    period_start: periodStart,
    period_end: periodEnd,
    period_label: nonEmptyString(data.period_label) ?? "Since first NAV",
    benchmark: nonEmptyString(data.benchmark) ?? "SPY",
    benchmark_total_return: finiteNumber(data.benchmark_total_return) ?? Number.NaN,
    trades_source: tradesSourceFrom(data),
    price_sources: priceSourcesFrom(data),
    methodology: methodologyFrom(data),
    summary,
    warnings: legacy ? [LEGACY_PAYLOAD_WARNING, ...warnings] : warnings,
    contracts_missing_history: stringList(data.contracts_missing_history),
    series,
    status: resolveStatus(data, series.length),
    flows_status: resolveFlowsStatus(data),
    nav_as_of: asOf,
    nav_sessions_behind: declaredSessionsBehind(data, asOf),
    ...(nonEmptyString(data.nav_source) ? { nav_source: String(data.nav_source) } : {}),
  };
}

/**
 * Accept payload v2 (schema_version 2) and the legacy reconstruction payload.
 * Runs exactly once, in usePerformance. Panels consume the result as-is.
 */
export function normalizePerformanceData(raw: unknown): PerformanceData | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  if (typeof data.error === "string" && data.series == null && data.status == null) return null;
  return isV2Payload(data) ? normalizeV2(data) : normalizeV1(data);
}

// ---------------------------------------------------------------------------
// Render view — the single decision layer both panels read
// ---------------------------------------------------------------------------

export type PerformanceRiskView = {
  volatility: GatedValue;
  sharpe_ratio: GatedValue;
  sortino_ratio: GatedValue;
  max_drawdown: GatedValue;
  current_drawdown: GatedValue;
  var_95: GatedValue;
  cvar_95: GatedValue;
};

export type PerformanceView = {
  status: PerformanceStatus;
  isDegraded: boolean;
  isStale: boolean;
  isInsufficient: boolean;
  warnings: PerformanceWarning[];
  errorWarnings: PerformanceWarning[];
  navAsOf: string;
  navSource: string;
  navSessionsBehind: number;
  flowsStatus: string;
  periodStart: string;
  periodEnd: string;
  periodLabel: string;
  asOf: string;
  lastSync: string;
  nReturns: number;
  calendarDays: number;
  /** null when the payload never measured it. A suppressed branch reports no
   *  equity at all; rendering it as $0.00 reads as a measured zero. */
  startingEquity: number | null;
  endingEquity: number | null;
  netExternalFlows: number | null;
  investmentPnl: number | null;
  twrCumReturn: number | null;
  annualized: GatedValue;
  mwrPeriod: GatedValue;
  mwrAnnualized: GatedValue;
  risk: PerformanceRiskView;
  benchmark: PerformanceBenchmarkBlock | null;
  benchmarkReason: GateReason;
  /** The reason as a renderable gate. Value is always null; the panels dash the
   *  beta / alpha / tracking-error cards and print this rather than deleting
   *  them, which is what made every benchmark reason unreachable on screen. */
  benchmarkGate: GatedValue;
  /** False only when there is no benchmark series at all (§C.2): then the cards
   *  are omitted entirely instead of dashed. */
  hasBenchmarkSeries: boolean;
  benchmarkSymbol: string;
  drawdownTroughDate: string;
  drawdownDurationDays: number;
  tradesSource: string;
  priceSources: { stocks: string; options: string };
  methodology: PerformanceData["methodology"];
  contractsMissingHistory: string[];
};

type RiskFreeMethodology = Pick<PerformanceData["methodology"], "risk_free_rate" | "risk_free_source">;

function riskFreePercent(methodology: RiskFreeMethodology): string {
  return `${(methodology.risk_free_rate * 100).toFixed(2)}%`;
}

/**
 * The source qualifier describes the SOURCE, never the rate. An unstated source
 * is not the zero fallback: printing "(fallback 0)" beside a measured 3.74%
 * contradicted the number next to it, and contradicted the Sharpe card's own
 * "DGS3MO" on the same payload. Both surfaces read these two functions so they
 * cannot disagree again.
 */
export function riskFreeMethodologyCopy(methodology: RiskFreeMethodology): string {
  const percent = riskFreePercent(methodology);
  if (methodology.risk_free_source === "fred_dgs3mo") return `${percent} (FRED DGS3MO)`;
  if (methodology.risk_free_rate > 0) return percent;
  return `${percent} (fallback 0)`;
}

/** The same statement, sized for a metric card's sub-label. */
export function riskFreeCardCopy(methodology: RiskFreeMethodology): string {
  const percent = riskFreePercent(methodology);
  if (methodology.risk_free_source === "fred_dgs3mo") return `RF ${percent} DGS3MO`;
  if (methodology.risk_free_rate > 0) return `RF ${percent}`;
  return `RF ${percent} fallback`;
}

function calendarDaysBetween(start: string, end: string): number {
  const from = Date.parse(`${start}T00:00:00Z`);
  const to = Date.parse(`${end}T00:00:00Z`);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return 0;
  return Math.max(0, Math.round((to - from) / 86_400_000));
}

function drawdownTrough(series: PerformanceSeriesPoint[]): string {
  if (series.length === 0) return "";
  return series.reduce((worst, point) => (point.drawdown < worst.drawdown ? point : worst), series[0]).date;
}

/** A degraded payload publishes no confident number; the reason survives. */
function suppressFor(gate: GatedValue, reason: GateReason): GatedValue {
  if (gate.value == null) return gate;
  return { value: null, n: gate.n, min_n: gate.min_n, unavailable_reason: reason };
}

function legacyGate(
  value: number | undefined,
  gated: boolean,
  n: number,
  minN: number,
): GatedValue {
  if (gated) return suppressed("insufficient_n", n, minN);
  if (value == null || !Number.isFinite(value)) return suppressed("not_computed", n, minN);
  return published(value, n, minN);
}

function benchmarkReasonFor(
  block: PerformanceBenchmarkBlock,
  nReturns: number,
  degradedReason: GateReason | null,
): GateReason | null {
  if (degradedReason) return degradedReason;
  if (isBenchmarkGated(block.n_common, nReturns)) {
    return block.n_common < TWR_GATES.MIN_N_BENCHMARK ? "insufficient_n" : "benchmark_coverage";
  }
  if (!Number.isFinite(block.benchmark_return)) return "benchmark_degenerate";
  if (isImplausibleAlpha(block.alpha_annualized)) return "implausible";
  return null;
}

type BenchmarkView = {
  benchmark: PerformanceBenchmarkBlock | null;
  reason: GateReason;
  gate: GatedValue;
  hasSeries: boolean;
};

/** The reasons that mean there was no comparable series to regress against at
 *  all. Every other reason means a series existed and its statistics were
 *  suppressed, which is a dashed card carrying the reason, not a deleted one. */
const BENCHMARK_SERIES_ABSENT_REASONS: ReadonlySet<string> = new Set([
  "benchmark_unavailable",
  "nav_unavailable",
]);

type BenchmarkSuppression = {
  reason: GateReason;
  nCommon: number;
  coverage: number | undefined;
};

/**
 * The builder suppresses a benchmark to `benchmark: null` and states WHY only
 * inside the BENCHMARK_UNAVAILABLE warning context. Reading the null alone
 * loses the reason, which is what made every benchmark gate string unreachable
 * on screen. Lift the reason and the sample it was measured on back out.
 */
function benchmarkSuppressionFrom(warnings: PerformanceWarning[]): BenchmarkSuppression | null {
  const warning = warnings.find((w) => w.code === "BENCHMARK_UNAVAILABLE");
  if (!warning) return null;
  const context = asRecord(warning.context);
  const reason = nonEmptyString(context.reason);
  if (!reason) return null;
  return {
    reason: reason as GateReason,
    nCommon: finiteNumber(context.n_common) ?? 0,
    coverage: finiteNumber(context.coverage),
  };
}

function resolveBenchmark(
  block: PerformanceBenchmarkBlock | null,
  nReturns: number,
  degradedReason: GateReason | null,
  warnings: PerformanceWarning[],
): BenchmarkView {
  const gateFor = (reason: GateReason, nCommon: number, coverage?: number): GatedValue => ({
    value: null,
    n: nCommon,
    min_n: TWR_GATES.MIN_N_BENCHMARK,
    unavailable_reason: reason,
    coverage,
  });
  if (!block) {
    const suppression = benchmarkSuppressionFrom(warnings);
    if (!suppression || BENCHMARK_SERIES_ABSENT_REASONS.has(suppression.reason)) {
      return {
        benchmark: null,
        reason: "benchmark_unavailable",
        gate: gateFor("benchmark_unavailable", suppression?.nCommon ?? 0),
        hasSeries: false,
      };
    }
    return {
      benchmark: null,
      reason: suppression.reason,
      gate: gateFor(suppression.reason, suppression.nCommon, suppression.coverage),
      hasSeries: true,
    };
  }
  const coverage = nReturns > 0 ? block.n_common / nReturns : block.coverage;
  const reason = benchmarkReasonFor(block, nReturns, degradedReason);
  if (reason) {
    return { benchmark: null, reason, gate: gateFor(reason, block.n_common, coverage), hasSeries: true };
  }
  return {
    benchmark: block,
    reason: "not_computed",
    gate: gateFor("not_computed", block.n_common, coverage),
    hasSeries: true,
  };
}

function riskFromV2(risk: Record<string, GatedValue>, nReturns: number): PerformanceRiskView {
  const pick = (key: string, minN: number): GatedValue =>
    risk[key] ?? suppressed("not_computed", nReturns, minN);
  return {
    volatility: pick("volatility", TWR_GATES.MIN_N_DISPERSION),
    sharpe_ratio: pick("sharpe_ratio", TWR_GATES.MIN_N_RATIO),
    sortino_ratio: pick("sortino_ratio", TWR_GATES.MIN_N_RATIO),
    max_drawdown: pick("max_drawdown", TWR_GATES.MIN_N_DISPERSION),
    current_drawdown: pick("current_drawdown", TWR_GATES.MIN_N_DISPERSION),
    var_95: pick("var_95", TWR_GATES.MIN_N_DISPERSION),
    cvar_95: pick("cvar_95", TWR_GATES.MIN_N_DISPERSION),
  };
}

function riskFromV1(summary: PerformanceSummary, nReturns: number): PerformanceRiskView {
  const dispersionGated = isDispersionGated(nReturns);
  const ratioGated = isRatioGated(nReturns);
  return {
    volatility: legacyGate(summary.annualized_volatility, dispersionGated, nReturns, TWR_GATES.MIN_N_DISPERSION),
    sharpe_ratio: legacyGate(summary.sharpe_ratio, ratioGated, nReturns, TWR_GATES.MIN_N_RATIO),
    sortino_ratio: legacyGate(summary.sortino_ratio, ratioGated, nReturns, TWR_GATES.MIN_N_RATIO),
    max_drawdown: legacyGate(summary.max_drawdown, dispersionGated, nReturns, TWR_GATES.MIN_N_DISPERSION),
    current_drawdown: legacyGate(summary.current_drawdown, dispersionGated, nReturns, TWR_GATES.MIN_N_DISPERSION),
    var_95: legacyGate(summary.var_95, dispersionGated, nReturns, TWR_GATES.MIN_N_DISPERSION),
    cvar_95: legacyGate(summary.cvar_95, dispersionGated, nReturns, TWR_GATES.MIN_N_DISPERSION),
  };
}

/**
 * One decision per number, taken once, for both panels.
 *
 * Tolerates a raw snapshot as well as a normalized one: a persisted TWR row
 * omits the reconstruction-only arrays and the panels must still render.
 */
export function buildPerformanceView(raw: unknown): PerformanceView | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as PerformanceData & Record<string, unknown>;
  const series = normalizeSeries(data.series);
  const summary = asRecord(data.summary) as PerformanceSummary;
  const isV2 = finiteNumber(data.schema_version) === 2;

  const counts = asRecord(data.counts);
  const nReturns = isV2
    ? (finiteNumber(counts.n_returns) ?? series.length)
    : (finiteNumber(summary.trading_days) ?? series.length);
  const periodStart = nonEmptyString(data.period_start) ?? series[0]?.date ?? "";
  const periodEnd = nonEmptyString(data.period_end) ?? series[series.length - 1]?.date ?? "";
  const calendarDays = isV2
    ? (finiteNumber(data.calendar_days) ?? calendarDaysBetween(periodStart, periodEnd))
    : calendarDaysBetween(periodStart, periodEnd);

  const legacy = isLegacyPayload(data);
  const status = resolveStatus(data, series.length);
  const flowsStatus = resolveFlowsStatus(data);
  const navAsOf = nonEmptyString(data.nav_as_of) ?? nonEmptyString(data.as_of) ?? periodEnd;
  // DECISION 5 — staleness is decided here, at read time, from the NAV date and
  // the clock. The payload's declared status word is not consulted: a snapshot
  // can say "ok" and still be months old by the time anyone reads it.
  const navSessionsBehind = resolveSessionsBehind(data, navAsOf);
  const isStale = status === "stale" || isBeyondStalenessBudget(navSessionsBehind);
  const isDegraded = status === "degraded";
  const degradedReason: GateReason | null = isDegraded
    ? (flowsStatus === "failed" ? "no_flow_data" : "not_computed")
    : null;
  const gate = (value: GatedValue): GatedValue =>
    degradedReason ? suppressFor(value, degradedReason) : value;

  const twrBlock = asRecord(data.twr);
  const mwrBlock = asRecord(data.mwr);
  const equityBlock = asRecord(data.equity);

  const rawCumReturn = isV2
    ? (finiteNumber(twrBlock.cum_return) ?? null)
    : (finiteNumber(summary.total_return) ?? null);
  const implausibleCumReturn = rawCumReturn != null && isImplausibleCumReturn(rawCumReturn, calendarDays);
  const twrCumReturn =
    flowsStatus === "failed" || isDegraded || isStale || implausibleCumReturn ? null : rawCumReturn;

  const rawAnnualized = isV2
    ? gatedValueFrom(twrBlock.annualized, TWR_GATES.MIN_CALENDAR_DAYS_ANNUALIZED)
    : isAnnualizationGated(calendarDays)
      ? suppressed("period_lt_1y", calendarDays, TWR_GATES.MIN_CALENDAR_DAYS_ANNUALIZED)
      : legacyGate(summary.annualized_return, false, calendarDays, TWR_GATES.MIN_CALENDAR_DAYS_ANNUALIZED);
  const plausibleAnnualized =
    rawAnnualized.value != null && isImplausibleAnnualized(rawAnnualized.value)
      ? suppressFor(rawAnnualized, "implausible")
      : rawAnnualized;

  const mwrPeriod = isV2
    ? gatedValueFrom(mwrBlock.period_return, TWR_GATES.MIN_N_MWR)
    : isMwrGated(nReturns)
      ? suppressed("insufficient_n", nReturns, TWR_GATES.MIN_N_MWR)
      : suppressed("not_computed", nReturns, TWR_GATES.MIN_N_MWR);
  const mwrAnnualized = isV2
    ? gatedValueFrom(mwrBlock.annualized, TWR_GATES.MIN_N_MWR)
    : suppressed("not_computed", nReturns, TWR_GATES.MIN_N_MWR);

  const risk = isV2 ? riskFromV2(gatedRecord(data.risk), nReturns) : riskFromV1(summary, nReturns);
  const payloadWarnings = performanceWarnings(data);
  const benchmarkView = resolveBenchmark(
    isV2 ? benchmarkBlockFrom(data.benchmark) : null,
    nReturns,
    degradedReason,
    payloadWarnings,
  );

  const startingEquity =
    finiteNumber(equityBlock.starting)
    ?? finiteNumber(summary.starting_equity)
    ?? firstNav(data.series)
    ?? null;
  const endingEquity =
    finiteNumber(equityBlock.ending)
    ?? finiteNumber(summary.ending_equity)
    ?? lastNav(data.series)
    ?? null;

  // normalizeV1 already prepended it on a normalized payload; stating the same
  // problem once per normalizer shows the operator two flags for one fault.
  const warnings = legacy && !payloadWarnings.some((w) => w.code === LEGACY_PAYLOAD_WARNING.code)
    ? [LEGACY_PAYLOAD_WARNING, ...payloadWarnings]
    : payloadWarnings;
  const drawdownDetail = asRecord(data.drawdown_detail);

  return {
    status,
    isDegraded,
    isStale,
    isInsufficient: status === "insufficient_data" || status === "unavailable",
    warnings,
    errorWarnings: warnings.filter((w) => w.severity === "error"),
    navAsOf,
    navSource: nonEmptyString(data.nav_source) ?? "",
    navSessionsBehind,
    flowsStatus,
    periodStart,
    periodEnd,
    periodLabel: nonEmptyString(data.period_label) ?? "Since first NAV",
    asOf: nonEmptyString(data.as_of) ?? nonEmptyString(data.nav_as_of) ?? periodEnd,
    lastSync: nonEmptyString(data.last_sync) ?? nonEmptyString(data.generated_at) ?? "",
    nReturns,
    calendarDays,
    startingEquity,
    endingEquity,
    netExternalFlows: finiteNumber(equityBlock.net_external_flows) ?? null,
    investmentPnl:
      finiteNumber(equityBlock.investment_pnl)
      ?? (isV2 ? null : (finiteNumber(summary.pnl) ?? null)),
    twrCumReturn,
    annualized: gate(plausibleAnnualized),
    mwrPeriod: gate(mwrPeriod),
    mwrAnnualized: gate(mwrAnnualized),
    risk: {
      volatility: gate(risk.volatility),
      sharpe_ratio: gate(risk.sharpe_ratio),
      sortino_ratio: gate(risk.sortino_ratio),
      max_drawdown: gate(risk.max_drawdown),
      current_drawdown: gate(risk.current_drawdown),
      var_95: gate(risk.var_95),
      cvar_95: gate(risk.cvar_95),
    },
    benchmark: benchmarkView.benchmark,
    benchmarkReason: benchmarkView.reason,
    benchmarkGate: benchmarkView.gate,
    hasBenchmarkSeries: benchmarkView.hasSeries,
    benchmarkSymbol:
      benchmarkView.benchmark?.symbol ?? (typeof data.benchmark === "string" ? data.benchmark : "SPY"),
    drawdownTroughDate: nonEmptyString(drawdownDetail.trough_date) ?? drawdownTrough(series),
    drawdownDurationDays:
      finiteNumber(summary.max_drawdown_duration_days) ?? drawdownDurationDays(series),
    tradesSource: tradesSourceFrom(data),
    priceSources: priceSourcesFrom(data),
    methodology: methodologyFrom(data),
    contractsMissingHistory: stringList(data.contracts_missing_history),
  };
}
