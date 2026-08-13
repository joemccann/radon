import { expect, test } from "vitest";
import {
  DEFAULT_PERFORMANCE_CHART_HEIGHT,
  DEFAULT_PERFORMANCE_CHART_MARGINS,
  DEFAULT_PERFORMANCE_CHART_WIDTH,
  buildPerformanceChartModel,
} from "../lib/performanceChart";
import type { PerformanceData } from "../lib/types";

const PERFORMANCE_MOCK: PerformanceData = {
  as_of: "2026-03-10",
  last_sync: "2026-03-10T18:55:00Z",
  period_start: "2026-01-01",
  period_end: "2026-03-10",
  period_label: "YTD",
  benchmark: "SPY",
  benchmark_total_return: -0.0225,
  trades_source: "ib_flex",
  price_sources: {
    stocks: "ib_with_uw_yahoo_fallback",
    options: "unusual_whales_option_contract_historic",
  },
  methodology: {
    curve_type: "reconstructed_net_liquidation",
    return_basis: "daily_close_to_close",
    risk_free_rate: 0,
    library_strategy: "in_repo_formulas_aligned_to_empyrical_quantstats_conventions",
  },
  summary: {
    starting_equity: 1_050_000,
    ending_equity: 1_094_500,
    pnl: 44_500,
    trading_days: 46,
    total_return: 0.04238,
    annualized_return: 0.264,
    annualized_volatility: 0.118,
    downside_deviation: 0.081,
    sharpe_ratio: 1.84,
    sortino_ratio: 2.41,
    calmar_ratio: 2.87,
    max_drawdown: -0.0918,
    current_drawdown: -0.0121,
    max_drawdown_duration_days: 14,
    beta: 0.62,
    alpha: 0.038,
    correlation: 0.77,
    r_squared: 0.59,
    tracking_error: 0.094,
    information_ratio: 0.88,
    treynor_ratio: 0.43,
    upside_capture: 0.91,
    downside_capture: 0.68,
    var_95: -0.012,
    cvar_95: -0.017,
    tail_ratio: 1.12,
    ulcer_index: 0.029,
    skew: -0.2,
    kurtosis: 0.41,
    hit_rate: 0.57,
    positive_days: 26,
    negative_days: 19,
    flat_days: 1,
    best_day: 0.019,
    worst_day: -0.016,
    average_up_day: 0.005,
    average_down_day: -0.004,
    win_loss_ratio: 1.25,
  },
  warnings: [],
  contracts_missing_history: [],
  series: Array.from({ length: 10 }, (_, index) => ({
    date: `2026-01-${String(index + 2).padStart(2, "0")}`,
    equity: 1_050_000 + index * 4_500,
    daily_return: index === 0 ? null : 0.0025,
    drawdown: index < 6 ? 0 : -0.01,
    benchmark_close: 670 + index * 2,
    benchmark_return: index === 0 ? 0 : 0.0014,
  })),
};

test("performance chart model builds visible x/y axis ticks", () => {
  const model = buildPerformanceChartModel(
    PERFORMANCE_MOCK,
    DEFAULT_PERFORMANCE_CHART_WIDTH,
    DEFAULT_PERFORMANCE_CHART_HEIGHT,
    DEFAULT_PERFORMANCE_CHART_MARGINS,
  );

  expect(model.yAxisTicks.length).toBeGreaterThanOrEqual(4);
  expect(model.xAxisTicks.length).toBeGreaterThanOrEqual(3);
  expect(model.yAxisTicks.every((tick) => tick.label.startsWith("$"))).toBe(true);
  expect(model.xAxisTicks[0]?.label).toMatch(/^[A-Z][a-z]{2} \d{1,2}$/);
});

test("performance chart model uses one shared domain for portfolio and rebased benchmark", () => {
  const model = buildPerformanceChartModel(
    PERFORMANCE_MOCK,
    DEFAULT_PERFORMANCE_CHART_WIDTH,
    DEFAULT_PERFORMANCE_CHART_HEIGHT,
    DEFAULT_PERFORMANCE_CHART_MARGINS,
  );
  const combined = [...PERFORMANCE_MOCK.series.map((point) => point.equity), ...model.rebasedBenchmarkValues];

  expect(model.domainMin).toBeLessThanOrEqual(Math.min(...combined));
  expect(model.domainMax).toBeGreaterThanOrEqual(Math.max(...combined));
  expect(model.equityPath.startsWith("M ")).toBe(true);
  expect(model.benchmarkPath.startsWith("M ")).toBe(true);
});

test("actual normalized TWR payload without legacy equity or benchmark fields stays finite", () => {
  const actualTwrPayload = {
    summary: { total_return: 0.03, trading_days: 3 },
    series: [
      { date: "2026-08-10", nav: 1_000_000, return: 0, cum_return: 0, equity: 100 },
      { date: "2026-08-11", nav: 1_020_000, return: 0.02, cum_return: 0.02, equity: 102 },
      { date: "2026-08-12", nav: 1_030_000, return: 0.0098, cum_return: 0.03, equity: 103 },
    ],
    benchmark: "SPY",
    warnings: [],
  } as unknown as PerformanceData;

  const model = buildPerformanceChartModel(actualTwrPayload);
  expect(model.equityPath).not.toContain("NaN");
  expect(model.areaPath).not.toContain("NaN");
  expect(model.benchmarkPath).toBe("");
  expect(model.yAxisTicks.every((tick) => Number.isFinite(tick.y) && Number.isFinite(tick.value))).toBe(true);
});
