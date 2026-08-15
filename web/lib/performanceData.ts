import type { PerformanceData, PerformanceSeriesPoint, PerformanceSummary } from "./types";

type RawPoint = {
  date?: unknown;
  equity?: unknown;
  nav?: unknown;
  return?: unknown;
  daily_return?: unknown;
  drawdown?: unknown;
  benchmark_close?: unknown;
  benchmark_return?: unknown;
};

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function tradesSourceFrom(raw: Record<string, unknown>): string {
  if (typeof raw.trades_source === "string" && raw.trades_source.length > 0) {
    return raw.trades_source;
  }
  return "ib_nav";
}

function priceSourcesFrom(raw: Record<string, unknown>): PerformanceData["price_sources"] {
  const sources = asRecord(raw.price_sources);
  return {
    stocks: typeof sources.stocks === "string" && sources.stocks.length > 0 ? sources.stocks : "ib_flex_nav",
    options: typeof sources.options === "string" && sources.options.length > 0 ? sources.options : "none",
  };
}

function normalizeSeries(raw: unknown): PerformanceSeriesPoint[] {
  if (!Array.isArray(raw)) return [];
  let peak = -Infinity;
  const series: PerformanceSeriesPoint[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const point = item as RawPoint;
    const date = typeof point.date === "string" ? point.date : "";
    const dollarEquity = finiteNumber(point.nav) ?? finiteNumber(point.equity);
    if (!date || dollarEquity == null) continue;
    if (dollarEquity > peak) peak = dollarEquity;
    const dailyReturn = finiteNumber(point.daily_return) ?? finiteNumber(point.return) ?? null;
    const drawdown = finiteNumber(point.drawdown) ?? (peak > 0 ? dollarEquity / peak - 1 : 0);
    series.push({
      date,
      equity: dollarEquity,
      daily_return: dailyReturn,
      drawdown,
      benchmark_close: finiteNumber(point.benchmark_close),
      benchmark_return: finiteNumber(point.benchmark_return),
    });
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
  const first = rawSeries[0] as RawPoint | undefined;
  return finiteNumber(first?.nav);
}

function lastNav(rawSeries: unknown): number | undefined {
  if (!Array.isArray(rawSeries) || rawSeries.length === 0) return undefined;
  const last = rawSeries[rawSeries.length - 1] as RawPoint | undefined;
  return finiteNumber(last?.nav);
}

/**
 * Accept the live TWR snapshot and the legacy reconstruction payload.
 * Persisted TWR rows omit reconstruction-only arrays; the panel must not throw.
 */
export function normalizePerformanceData(raw: unknown): PerformanceData | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  if (typeof data.error === "string" && data.series == null && data.status == null) return null;

  const series = normalizeSeries(data.series);
  const warnings = stringList(data.warnings);
  const summaryRaw = asRecord(data.summary);
  const metrics = asRecord(data.metrics);
  const methodologyRaw = asRecord(data.methodology);
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
    (typeof data.period_start === "string" && data.period_start) ||
    (typeof summaryRaw.period_start === "string" && summaryRaw.period_start) ||
    series[0]?.date ||
    "";
  const periodEnd =
    (typeof data.period_end === "string" && data.period_end) ||
    (typeof summaryRaw.period_end === "string" && summaryRaw.period_end) ||
    series[series.length - 1]?.date ||
    "";
  const asOf = typeof data.as_of === "string" && data.as_of.length > 0 ? data.as_of : periodEnd;
  const lastSync = typeof data.last_sync === "string" && data.last_sync.length > 0 ? data.last_sync : asOf;

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

  return {
    as_of: asOf,
    last_sync: lastSync,
    period_start: periodStart,
    period_end: periodEnd,
    period_label: typeof data.period_label === "string" && data.period_label.length > 0 ? data.period_label : "Since first NAV",
    benchmark: typeof data.benchmark === "string" && data.benchmark.length > 0 ? data.benchmark : "SPY",
    benchmark_total_return: finiteNumber(data.benchmark_total_return) ?? Number.NaN,
    trades_source: tradesSourceFrom(data),
    price_sources: priceSourcesFrom(data),
    methodology: {
      curve_type: typeof methodologyRaw.curve_type === "string" ? methodologyRaw.curve_type : "twr_modified_dietz_daily",
      return_basis: typeof methodologyRaw.return_basis === "string" ? methodologyRaw.return_basis : "time_weighted",
      risk_free_rate: finiteNumber(methodologyRaw.risk_free_rate) ?? 0,
      library_strategy: typeof methodologyRaw.library_strategy === "string" ? methodologyRaw.library_strategy : "fred_dgs3mo",
    },
    summary,
    warnings,
    contracts_missing_history: stringList(data.contracts_missing_history),
    series,
  };
}
