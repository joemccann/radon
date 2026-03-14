"use client";

import { AlertTriangle, Gauge, ShieldAlert, Sigma, TrendingDown } from "lucide-react";
import AttributionPanel from "./AttributionPanel";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  DEFAULT_PERFORMANCE_CHART_HEIGHT,
  DEFAULT_PERFORMANCE_CHART_MARGINS,
  DEFAULT_PERFORMANCE_CHART_WIDTH,
  buildPerformanceChartModel,
} from "@/lib/performanceChart";
import { fmtUsd, fmtUsdExact, fmtPct, fmtRatio, toneClass } from "@/lib/format";
import { isPerformanceBehindPortfolioSync } from "@/lib/performanceFreshness";
import type { PerformanceData, PerformanceSeriesPoint } from "@/lib/types";
import { usePerformance } from "@/lib/usePerformance";
import ChartPanel from "./charts/ChartPanel";
import MetricDefinitionModal from "./MetricDefinitionModal";

type PerformanceCardConfig = {
  id: string;
  label: string;
  title: string;
  value: string;
  change: string;
  definition: string;
  formula: string;
  tone?: "positive" | "negative" | "neutral";
};

function StatCard({
  id,
  label,
  value,
  change,
  definition,
  formula,
  onClick,
  tone = "neutral",
}: {
  id: string;
  label: string;
  value: string;
  change: string;
  definition: string;
  formula: string;
  onClick: () => void;
  tone?: "positive" | "negative" | "neutral";
}) {
  return (
    <button
      type="button"
      className="metric-card metric-card-clickable performance-card-trigger"
      data-testid={`performance-card-${id}`}
      aria-label={`${label} metric details`}
      data-definition={definition}
      data-formula={formula}
      onClick={onClick}
    >
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${tone !== "neutral" ? tone : ""}`}>{value}</div>
      <div className={`metric-change ${tone}`}>{change}</div>
    </button>
  );
}

function PerformanceChart({ data }: { data: PerformanceData }) {
  const {
    equityPath,
    benchmarkPath,
    areaPath,
    latestEquity,
    latestBenchmark,
    yAxisTicks,
    xAxisTicks,
    plotBottom,
    plotLeft,
    plotRight,
  } = useMemo(
    () => buildPerformanceChartModel(data, DEFAULT_PERFORMANCE_CHART_WIDTH, DEFAULT_PERFORMANCE_CHART_HEIGHT),
    [data],
  );

  return (
    <ChartPanel
      family="analytical-time-series"
      title="YTD Equity Curve"
      badge={<span className="pill neutral">{data.series.length} SESSIONS</span>}
      legend={[
        { label: "Portfolio", role: "primary" },
        { label: `${data.benchmark} rebased`, role: "comparison" },
      ]}
      bodyClassName="performance-chart-shell"
      dataTestId="performance-chart-panel"
    >
        <svg
          data-testid="performance-equity-chart"
          viewBox={`0 0 ${DEFAULT_PERFORMANCE_CHART_WIDTH} ${DEFAULT_PERFORMANCE_CHART_HEIGHT}`}
          className="performance-chart"
          role="img"
          aria-label="YTD portfolio equity curve versus benchmark"
        >
          <defs>
            <linearGradient id="performanceAreaGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--chart-fill-primary-start)" />
              <stop offset="100%" stopColor="var(--chart-fill-primary-end)" />
            </linearGradient>
          </defs>
          {yAxisTicks.map((tick) => {
            const isBaseline = Math.abs(tick.y - plotBottom) < 0.5;
            return (
              <line
                key={tick.value}
                x1={plotLeft}
                x2={plotRight}
                y1={tick.y}
                y2={tick.y}
                className={isBaseline ? "pa-l" : "performance-grid-line"}
              />
            );
          })}
          <g data-testid="performance-y-axis">
            <line
              x1={plotLeft}
              x2={plotLeft}
              y1={DEFAULT_PERFORMANCE_CHART_MARGINS.top}
              y2={plotBottom}
              className="pa-l"
            />
            {yAxisTicks.map((tick) => (
              <g key={`y-${tick.value}`} className="performance-axis-tick">
                <line x1={plotLeft - 6} x2={plotLeft} y1={tick.y} y2={tick.y} className="pa-l" />
                <text
                  x={plotLeft - 12}
                  y={tick.y}
                  textAnchor="end"
                  dominantBaseline="middle"
                  className="performance-axis-label"
                  data-testid="performance-axis-y-label"
                >
                  {tick.label}
                </text>
              </g>
            ))}
          </g>
          <path d={areaPath} fill="url(#performanceAreaGradient)" />
          <path d={benchmarkPath} className="performance-line performance-line-benchmark" />
          <path d={equityPath} className="performance-line performance-line-equity" />
          <g data-testid="performance-x-axis">
            <line x1={plotLeft} x2={plotRight} y1={plotBottom} y2={plotBottom} className="pa-l" />
            {xAxisTicks.map((tick, index) => (
              <g key={`x-${tick.index}`} className="performance-axis-tick">
                <line x1={tick.x} x2={tick.x} y1={plotBottom} y2={plotBottom + 6} className="pa-l" />
                <text
                  x={tick.x}
                  y={plotBottom + 18}
                  textAnchor={index === 0 ? "start" : index === xAxisTicks.length - 1 ? "end" : "middle"}
                  className="performance-axis-label"
                  data-testid="performance-axis-x-label"
                >
                  {tick.label}
                </text>
              </g>
            ))}
          </g>
        </svg>
        <div className="performance-chart-meta">
          <div className="performance-meta-item">
            <span className="pm-l">Portfolio</span>
            <span className="pm-v">{fmtUsdExact(latestEquity)}</span>
          </div>
          <div className="performance-meta-item">
            <span className="pm-l">{data.benchmark} Rebased</span>
            <span className="pm-v">{fmtUsdExact(latestBenchmark)}</span>
          </div>
          <div className="performance-meta-item">
            <span className="pm-l">Benchmark Return</span>
            <span className={`pm-v ${toneClass(data.benchmark_total_return)}`}>{fmtPct(data.benchmark_total_return)}</span>
          </div>
        </div>
    </ChartPanel>
  );
}

function drawdownLeader(series: PerformanceSeriesPoint[]): string {
  if (series.length === 0) return "---";
  const worst = series.reduce((acc, point) => (point.drawdown < acc.drawdown ? point : acc), series[0]);
  return worst?.date ?? "---";
}

export default function PerformancePanel({ portfolioLastSync = null }: { portfolioLastSync?: string | null }) {
  const { data, loading, error, syncNow } = usePerformance(true);
  const [activeCardId, setActiveCardId] = useState<string | null>(null);
  const requestedPortfolioSyncRef = useRef<string | null>(null);

  useEffect(() => {
    if (!data || !portfolioLastSync) return;
    if (!isPerformanceBehindPortfolioSync(data, portfolioLastSync)) return;
    if (requestedPortfolioSyncRef.current === portfolioLastSync) return;

    requestedPortfolioSyncRef.current = portfolioLastSync;
    syncNow();
  }, [data, portfolioLastSync, syncNow]);

  const cardConfigs = useMemo<PerformanceCardConfig[]>(() => {
    if (!data) return [];
    const { summary } = data;

    return [
      {
        id: "ytd-return",
        label: "YTD Return",
        title: "YTD Return",
        value: fmtPct(summary.total_return),
        change: `${fmtUsd(summary.pnl)} P&L`,
        tone: toneClass(summary.total_return),
        definition: "Cumulative return from the first trading session of the year through the current portfolio snapshot.",
        formula:
          "YTD Return = (Ending Equity / Starting Equity) - 1\n" +
          "P&L = Ending Equity - Starting Equity\n" +
          "Ending Equity and Starting Equity come from the reconstructed YTD net-liq curve.",
      },
      {
        id: "sharpe-ratio",
        label: "Sharpe Ratio",
        title: "Sharpe Ratio",
        value: fmtRatio(summary.sharpe_ratio),
        change: `VOL ${fmtPct(summary.annualized_volatility)}`,
        tone: toneClass(summary.sharpe_ratio),
        definition: "Risk-adjusted return per unit of total volatility. Higher values mean more return for each unit of realized risk.",
        formula:
          "Sharpe Ratio = Mean(Daily Returns - Risk Free Rate / 252) / StdDev(Daily Returns) * sqrt(252)\n" +
          "Annualized Volatility = StdDev(Daily Returns) * sqrt(252)\n" +
          "Current methodology uses Risk Free Rate = 0.00%.",
      },
      {
        id: "sortino-ratio",
        label: "Sortino Ratio",
        title: "Sortino Ratio",
        value: fmtRatio(summary.sortino_ratio),
        change: `DN DEV ${fmtPct(summary.downside_deviation)}`,
        tone: toneClass(summary.sortino_ratio),
        definition: "Risk-adjusted return that only penalizes downside volatility. It ignores upside variation and focuses on harmful drawdowns.",
        formula:
          "Sortino Ratio = Mean(Daily Returns - Risk Free Rate / 252) / Downside Deviation * sqrt(252)\n" +
          "Downside Deviation = sqrt(mean(min(Daily Return, 0)^2)) * sqrt(252)\n" +
          "Current methodology uses Risk Free Rate = 0.00%.",
      },
      {
        id: "max-drawdown",
        label: "Max Drawdown",
        title: "Max Drawdown",
        value: fmtPct(summary.max_drawdown),
        change: `${summary.max_drawdown_duration_days} DAYS`,
        tone: toneClass(summary.max_drawdown),
        definition: "Largest peak-to-trough decline in the reconstructed YTD equity curve. It measures the worst historical capital drawdown so far this year.",
        formula:
          "Drawdown_t = (Equity_t / Running Peak_t) - 1\n" +
          "Max Drawdown = minimum Drawdown_t over the YTD curve\n" +
          "Duration = consecutive sessions spent below the prior peak.",
      },
      {
        id: "beta",
        label: "Beta",
        title: "Beta",
        value: fmtRatio(summary.beta),
        change: data.benchmark,
        tone: toneClass(summary.beta - 1),
        definition: `Sensitivity of the portfolio's daily returns to ${data.benchmark}'s daily returns. A beta above 1 implies amplified market sensitivity; below 1 implies lower sensitivity.`,
        formula:
          `Beta = Covariance(Portfolio Returns, ${data.benchmark} Returns) / Variance(${data.benchmark} Returns)\n` +
          `Returns are daily close-to-close returns from the reconstructed portfolio curve and ${data.benchmark} benchmark series.`,
      },
      {
        id: "alpha",
        label: "Alpha",
        title: "Alpha",
        value: fmtPct(summary.alpha),
        change: "ANNUALIZED",
        tone: toneClass(summary.alpha),
        definition: `Annualized excess return after adjusting for ${data.benchmark} beta. Positive alpha means the portfolio outperformed what its market exposure alone would imply.`,
        formula:
          `Alpha = (Mean(Portfolio Returns) - Beta * Mean(${data.benchmark} Returns)) * 252\n` +
          "The output is annualized from daily return differentials.",
      },
      {
        id: "information-ratio",
        label: "Information Ratio",
        title: "Information Ratio",
        value: fmtRatio(summary.information_ratio),
        change: `TE ${fmtPct(summary.tracking_error)}`,
        tone: toneClass(summary.information_ratio),
        definition: `Active return per unit of benchmark-relative volatility. It measures how efficiently the portfolio is outperforming or underperforming ${data.benchmark}.`,
        formula:
          `Active Return_t = Portfolio Return_t - ${data.benchmark} Return_t\n` +
          "Tracking Error = StdDev(Active Return) * sqrt(252)\n" +
          "Information Ratio = Mean(Active Return) / StdDev(Active Return) * sqrt(252)",
      },
      {
        id: "calmar-ratio",
        label: "Calmar Ratio",
        title: "Calmar Ratio",
        value: fmtRatio(summary.calmar_ratio),
        change: `CUR DD ${fmtPct(summary.current_drawdown)}`,
        tone: toneClass(summary.calmar_ratio),
        definition: "Annualized return scaled by the worst drawdown. It answers how much return the portfolio generated relative to the maximum capital drawdown endured.",
        formula:
          "Calmar Ratio = Annualized Return / abs(Max Drawdown)\n" +
          "Current Drawdown = (Latest Equity / Running Peak) - 1\n" +
          "A higher Calmar Ratio means better return relative to drawdown pain.",
      },
    ];
  }, [data]);
  const activeCard = useMemo(
    () => cardConfigs.find((card) => card.id === activeCardId) ?? null,
    [activeCardId, cardConfigs],
  );

  if (loading && !data) {
    return (
      <div className="section">
        <div className="s-hd">
          <div className="s-tt">
            <Gauge size={14} />
            Performance
          </div>
          <span className="pill neutral">LOADING</span>
        </div>
        <div className="s-bd pe">
          Reconstructing YTD portfolio performance...
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="section">
        <div className="s-hd">
          <div className="s-tt">
            <ShieldAlert size={14} />
            Performance
          </div>
          <span className="pill undefined">UNAVAILABLE</span>
        </div>
        <div className="s-bd pe">
          {error ?? "No performance data available."}
        </div>
      </div>
    );
  }

  const { summary } = data;

  return (
    <div className="performance-panel" data-testid="performance-panel">
      <div className="section performance-hero">
        <div className="s-bd performance-hero-body">
          <div>
            <div className="section-label-mono">RECONSTRUCTED {data.period_label}</div>
            <div className="performance-hero-value">
              <span className={toneClass(summary.total_return)}>{fmtPct(summary.total_return)}</span>
            </div>
            <div className="performance-hero-subtitle">
              Ending equity {fmtUsdExact(summary.ending_equity)} • {data.benchmark} {fmtPct(data.benchmark_total_return)} • as of {data.as_of}
            </div>
          </div>
          <div className="performance-hero-pills">
            <span className="pill neutral">{data.trades_source === "ib_flex" ? "IB FLEX" : "CACHE"}</span>
            <span className="pill neutral">{summary.trading_days} DAYS</span>
            <span className={`pill ${summary.max_drawdown < -0.1 ? "undefined" : "defined"}`}>MAX DD {fmtPct(summary.max_drawdown)}</span>
          </div>
        </div>
      </div>

      <div className="section">
        <div className="s-hd">
          <div className="s-tt">
            <Gauge size={14} />
            Core Performance
          </div>
          <span className="pill defined">INSTITUTIONAL</span>
        </div>
        <div className="s-bd">
          <div className="mg">
            {cardConfigs.slice(0, 4).map((card) => (
              <StatCard key={card.id} {...card} onClick={() => setActiveCardId(card.id)} />
            ))}
          </div>

          <div className="mg">
            {cardConfigs.slice(4).map((card) => (
              <StatCard key={card.id} {...card} onClick={() => setActiveCardId(card.id)} />
            ))}
          </div>
        </div>
      </div>

      <PerformanceChart data={data} />

      <div className="pg-2">
        <div className="section">
          <div className="s-hd">
            <div className="s-tt">
              <TrendingDown size={14} />
              Tail And Path Risk
            </div>
            <span className="pill neutral">DAILY</span>
          </div>
          <div className="s-bd">
            <div className="performance-metric-list">
              <div><span>VaR 95%</span><strong>{fmtPct(summary.var_95)}</strong></div>
              <div><span>CVaR 95%</span><strong>{fmtPct(summary.cvar_95)}</strong></div>
              <div><span>Tail Ratio</span><strong>{fmtRatio(summary.tail_ratio)}</strong></div>
              <div><span>Ulcer Index</span><strong>{fmtRatio(summary.ulcer_index)}</strong></div>
              <div><span>Worst Day</span><strong>{fmtPct(summary.worst_day)}</strong></div>
              <div><span>Drawdown Trough</span><strong>{drawdownLeader(data.series)}</strong></div>
            </div>
          </div>
        </div>

        <div className="section">
          <div className="s-hd">
            <div className="s-tt">
              <Sigma size={14} />
              Distribution And Capture
            </div>
            <span className="pill neutral">{data.benchmark}</span>
          </div>
          <div className="s-bd">
            <div className="performance-metric-list">
              <div><span>Hit Rate</span><strong>{fmtPct(summary.hit_rate)}</strong></div>
              <div><span>Upside Capture</span><strong>{fmtRatio(summary.upside_capture)}</strong></div>
              <div><span>Downside Capture</span><strong>{fmtRatio(summary.downside_capture)}</strong></div>
              <div><span>Correlation</span><strong>{fmtRatio(summary.correlation)}</strong></div>
              <div><span>Skew</span><strong>{fmtRatio(summary.skew)}</strong></div>
              <div><span>Kurtosis</span><strong>{fmtRatio(summary.kurtosis)}</strong></div>
            </div>
          </div>
        </div>
      </div>

      <div className="pg-2">
        <div className="section">
          <div className="s-hd">
            <div className="s-tt">
              <AlertTriangle size={14} />
              Methodology
            </div>
            <span className="pill neutral">{data.methodology.return_basis.replace(/_/g, " ").toUpperCase()}</span>
          </div>
          <div className="s-bd performance-meta-grid">
            <div className="performance-meta-item">
              <span className="pm-l">Curve Type</span>
              <span className="pm-v">{data.methodology.curve_type.replace(/_/g, " ")}</span>
            </div>
            <div className="performance-meta-item">
              <span className="pm-l">Stock History</span>
              <span className="pm-v">{data.price_sources.stocks}</span>
            </div>
            <div className="performance-meta-item">
              <span className="pm-l">Option History</span>
              <span className="pm-v">{data.price_sources.options}</span>
            </div>
            <div className="performance-meta-item">
              <span className="pm-l">Risk-Free Assumption</span>
              <span className="pm-v">{fmtPct(data.methodology.risk_free_rate)}</span>
            </div>
          </div>
        </div>

        <div className="section">
          <div className="s-hd">
            <div className="s-tt">
              <AlertTriangle size={14} />
              Warnings
            </div>
            <span className="pill undefined">{data.warnings.length} FLAGS</span>
          </div>
          <div className="s-bd">
            <ul className="performance-note-list">
              {data.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
              {data.contracts_missing_history.length > 0 && (
                <li>{data.contracts_missing_history.length} contract(s) were missing historical marks and were marked to zero where no price history was available.</li>
              )}
            </ul>
          </div>
        </div>
      </div>

      <AttributionPanel />

      {activeCard && (
        <MetricDefinitionModal
          open
          title={activeCard.title}
          value={activeCard.value}
          definition={activeCard.definition}
          formula={activeCard.formula}
          onClose={() => setActiveCardId(null)}
        />
      )}
    </div>
  );
}
