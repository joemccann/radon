import type {
  PerformanceData,
  PerformanceGatedValue,
  PerformanceSeriesPoint,
} from "@/lib/types";
import { businessDateKeys } from "./time";

const SESSION_COUNT = 270;

function round(value: number, places = 6): number {
  const scale = 10 ** places;
  return Math.round(value * scale) / scale;
}

function gated(value: number, n: number, minN: number): PerformanceGatedValue {
  return { value: round(value), n, min_n: minN, unavailable_reason: null };
}

function standardDeviation(values: number[]): number {
  const average = mean(values);
  return Math.sqrt(
    values.reduce((sum, value) => sum + (value - average) ** 2, 0) / values.length,
  );
}

function mean(values: number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function covariance(left: number[], right: number[]): number {
  const leftMean = mean(left);
  const rightMean = mean(right);
  return left.reduce(
    (sum, value, index) => sum + (value - leftMean) * (right[index] - rightMean),
    0,
  ) / left.length;
}

function calendarDaysBetween(start: string, end: string): number {
  return Math.round(
    (Date.parse(`${end}T12:00:00.000Z`) - Date.parse(`${start}T12:00:00.000Z`)) / 86_400_000,
  );
}

/** A request-time sample account curve. No operator NAV, files, or services. */
export function buildDemoPerformance(now: Date = new Date()): PerformanceData {
  const dates = businessDateKeys(SESSION_COUNT, now);
  const starting = 1_000_000;
  let nav = starting;
  let benchmarkClose = 585;
  let highWater = starting;
  const benchmarkStart = benchmarkClose;
  let maxDrawdown = 0;
  let activePeakDate = dates[0];
  let maxDrawdownPeakDate = dates[0];
  let maxDrawdownTroughDate = dates[0];
  const returns: number[] = [];
  const benchmarkReturns: number[] = [];

  const series: PerformanceSeriesPoint[] = dates.map((date, index) => {
    const dailyReturn = index === 0
      ? null
      : 0.00042 + Math.sin(index * 0.41) * 0.0017 + Math.cos(index * 0.13) * 0.0009;
    const benchmarkReturn = index === 0
      ? 0
      : 0.00034 + Math.sin(index * 0.29) * 0.00145 + Math.cos(index * 0.09) * 0.00075;
    if (dailyReturn != null) {
      returns.push(dailyReturn);
      benchmarkReturns.push(benchmarkReturn);
      nav *= 1 + dailyReturn;
      benchmarkClose *= 1 + benchmarkReturn;
    }
    if (nav >= highWater) {
      highWater = nav;
      activePeakDate = date;
    }
    const drawdown = nav / highWater - 1;
    if (drawdown < maxDrawdown) {
      maxDrawdown = drawdown;
      maxDrawdownPeakDate = activePeakDate;
      maxDrawdownTroughDate = date;
    }
    return {
      date,
      equity: round(nav, 2),
      nav: round(nav, 2),
      daily_return: dailyReturn == null ? null : round(dailyReturn),
      drawdown: round(drawdown),
      twr_index: round((nav / starting) * 100, 4),
      cum_return: round(nav / starting - 1),
      flow: 0,
      skipped: false,
      benchmark_close: round(benchmarkClose, 2),
      benchmark_return: round(benchmarkReturn),
    };
  });

  const periodStart = dates[0];
  const periodEnd = dates.at(-1) ?? periodStart;
  const calendarDays = calendarDaysBetween(periodStart, periodEnd);
  const totalReturn = nav / starting - 1;
  const annualized = (1 + totalReturn) ** (365 / calendarDays) - 1;
  const volatility = standardDeviation(returns) * Math.sqrt(252);
  const downside = returns.filter((value) => value < 0);
  const downsideDeviation = standardDeviation(downside) * Math.sqrt(252);
  const riskFreeRate = 0.043;
  const sharpe = (annualized - riskFreeRate) / volatility;
  const sortino = (annualized - riskFreeRate) / downsideDeviation;
  const positiveDays = returns.filter((value) => value > 0).length;
  const negativeDays = returns.filter((value) => value < 0).length;
  const benchmarkTotalReturn = benchmarkClose / benchmarkStart - 1;
  const benchmarkAnnualized = (1 + benchmarkTotalReturn) ** (365 / calendarDays) - 1;
  const portfolioStdDev = standardDeviation(returns);
  const benchmarkStdDev = standardDeviation(benchmarkReturns);
  const returnCovariance = covariance(returns, benchmarkReturns);
  const correlation = returnCovariance / (portfolioStdDev * benchmarkStdDev);
  const beta = returnCovariance / benchmarkStdDev ** 2;
  const trackingError = standardDeviation(
    returns.map((value, index) => value - benchmarkReturns[index]),
  ) * Math.sqrt(252);
  const activeAnnualized = annualized - benchmarkAnnualized;
  const troughIndex = series.findIndex((point) => point.date === maxDrawdownTroughDate);
  const recoveryPoint = series.slice(troughIndex + 1).find((point) => point.drawdown === 0);
  const generatedAt = now.toISOString();

  return {
    schema_version: 2,
    status: "ok",
    generated_at: generatedAt,
    account_id: "DEMO",
    nav_source: "sample_data",
    nav_as_of: periodEnd,
    nav_sessions_behind: 0,
    flows_status: "ok",
    flows_source: "sample_data",
    calendar_days: calendarDays,
    counts: {
      n_nav_observations: series.length,
      n_subperiods: returns.length,
      n_returns: returns.length,
      n_skipped: 0,
      n_suspect: 0,
    },
    twr: {
      cum_return: round(totalReturn),
      annualized: gated(annualized, calendarDays, 365),
      excludes_suspect: true,
    },
    mwr: {
      period_return: gated(totalReturn, returns.length, 2),
      annualized: gated(annualized, returns.length, 2),
      multiple_sign_changes: false,
    },
    risk: {
      volatility: gated(volatility, returns.length, 20),
      sharpe_ratio: gated(sharpe, returns.length, 20),
      sortino_ratio: gated(sortino, returns.length, 20),
      max_drawdown: gated(maxDrawdown, returns.length, 2),
      current_drawdown: gated(series.at(-1)?.drawdown ?? 0, returns.length, 2),
      var_95: gated(-0.0022, returns.length, 20),
      cvar_95: gated(-0.0031, returns.length, 20),
    },
    distribution: {
      hit_rate: gated(positiveDays / returns.length, returns.length, 20),
      best_day: gated(Math.max(...returns), returns.length, 20),
      worst_day: gated(Math.min(...returns), returns.length, 20),
    },
    drawdown_detail: {
      peak_date: maxDrawdownPeakDate,
      trough_date: maxDrawdownTroughDate,
      trough_days: calendarDaysBetween(maxDrawdownPeakDate, maxDrawdownTroughDate),
      recovery_days: recoveryPoint
        ? calendarDaysBetween(maxDrawdownTroughDate, recoveryPoint.date)
        : null,
      ongoing: !recoveryPoint,
    },
    equity: {
      starting,
      ending: round(nav, 2),
      net_external_flows: 0,
      investment_pnl: round(nav - starting, 2),
    },
    benchmark: {
      symbol: "SPY",
      n_common: returns.length,
      coverage: 1,
      benchmark_return: round(benchmarkTotalReturn),
      beta: round(beta),
      alpha_annualized: round(activeAnnualized),
      correlation: round(correlation),
      r_squared: round(correlation ** 2),
      tracking_error: round(trackingError),
      information_ratio: round(activeAnnualized / trackingError),
      basis: "sample_data",
      low_confidence: false,
    },
    subperiods: [],
    as_of: periodEnd,
    last_sync: generatedAt,
    period_start: periodStart,
    period_end: periodEnd,
    period_label: "Trailing 270 sessions",
    benchmark_total_return: round(benchmarkTotalReturn),
    trades_source: "Sample data",
    price_sources: { stocks: "Sample data", options: "Sample data" },
    methodology: {
      curve_type: "twr_modified_dietz_daily",
      return_basis: "time_weighted",
      risk_free_rate: riskFreeRate,
      library_strategy: "sample_curve",
      risk_free_source: "Sample data",
      flow_convention: "beginning_of_day",
      day_count: "ACT/365",
    },
    summary: {
      starting_equity: starting,
      ending_equity: round(nav, 2),
      pnl: round(nav - starting, 2),
      trading_days: returns.length,
      total_return: round(totalReturn),
      annualized_return: round(annualized),
      annualized_volatility: round(volatility),
      downside_deviation: round(downsideDeviation),
      sharpe_ratio: round(sharpe),
      sortino_ratio: round(sortino),
      max_drawdown: round(maxDrawdown),
      current_drawdown: series.at(-1)?.drawdown ?? 0,
      positive_days: positiveDays,
      negative_days: negativeDays,
      flat_days: returns.length - positiveDays - negativeDays,
      hit_rate: round(positiveDays / returns.length),
      best_day: round(Math.max(...returns)),
      worst_day: round(Math.min(...returns)),
    },
    warnings: [],
    contracts_missing_history: [],
    series,
  };
}
