"use client";

import { Activity, AlertTriangle, Gauge, History, ShieldAlert, TrendingDown } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  DEFAULT_PERFORMANCE_CHART_HEIGHT,
  DEFAULT_PERFORMANCE_CHART_MARGINS,
  DEFAULT_PERFORMANCE_CHART_WIDTH,
  buildPerformanceChartModel,
} from "@/lib/performanceChart";
import {
  buildPerformanceView,
  riskFreeCardCopy,
  riskFreeMethodologyCopy,
  type PerformanceView,
} from "@/lib/performanceData";
import { isPerformanceBehindPortfolioSync } from "@/lib/performanceFreshness";
import { gateCopy, TWR_GATES, type GatedValue } from "@/lib/performanceTwr";
import type { PerformanceData } from "@/lib/types";
import { usePerformance } from "@/lib/usePerformance";
import { MarketState } from "@/lib/useMarketHours";
import { useViewport } from "@/lib/useViewport";
import ChartPanel from "./charts/ChartPanel";
import MetricDefinitionModal from "./MetricDefinitionModal";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import MobilePerformancePanel from "./mobile/MobilePerformancePanel";

type PerformancePanelProps = {
  portfolioLastSync?: string | null;
  marketState?: MarketState;
};

/** Suppressed value marker. Never a fabricated zero. */
const EMPTY = "--";

function fmtUsd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return EMPTY;
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${value < 0 ? "-" : ""}$${(abs / 1_000_000).toFixed(2)}M`;
  return `${value < 0 ? "-" : ""}$${abs.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

function fmtUsdExact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return EMPTY;
  return `${value < 0 ? "-" : ""}$${Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtPct(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return EMPTY;
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
}

function fmtPctNoSign(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return EMPTY;
  return `${(value * 100).toFixed(digits)}%`;
}

function fmtRatio(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return EMPTY;
  return value.toFixed(2);
}

function toneClass(value: number | null | undefined): "positive" | "negative" | "neutral" {
  if (value == null || !Number.isFinite(value)) return "neutral";
  return value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
}

type CardDef = {
  id: string;
  label: string;
  title: string;
  value: string;
  sub: string;
  tone: "positive" | "negative" | "neutral";
  definition: string;
  formula: string;
};

type CardSpec = {
  id: string;
  label: string;
  gate: GatedValue;
  format: (value: number) => string;
  presentSub: string;
  tone?: (value: number) => "positive" | "negative" | "neutral";
  definition: string;
  formula: string;
};

/** One card, one GatedValue, one copy generator. A missing value can only ever
 *  render the reason the payload gave for its absence. */
function toCard(spec: CardSpec): CardDef {
  const value = spec.gate.value;
  const present = value != null && Number.isFinite(value);
  return {
    id: spec.id,
    label: spec.label,
    title: spec.label,
    value: present ? spec.format(value) : EMPTY,
    sub: present ? spec.presentSub : gateCopy(spec.gate, spec.label),
    tone: present ? (spec.tone ?? toneClass)(value) : "neutral",
    definition: spec.definition,
    formula: spec.formula,
  };
}

function MetricCard({ card, onClick }: { card: CardDef; onClick: () => void }) {
  return (
    <button
      type="button"
      className="metric-card metric-card-clickable performance-card-trigger"
      data-testid={`performance-card-${card.id}`}
      aria-label={`${card.label} metric details`}
      data-definition={card.definition}
      data-formula={card.formula}
      onClick={onClick}
    >
      <div className="metric-label">{card.label}</div>
      <div className={`metric-value ${card.tone !== "neutral" ? card.tone : ""}`} data-testid={`performance-value-${card.id}`}>
        {card.value}
      </div>
      <div className={`metric-change ${card.tone}`} data-testid={`performance-sub-${card.id}`}>
        {card.sub}
      </div>
    </button>
  );
}

function PerformanceChart({ data, view }: { data: PerformanceData; view: PerformanceView }) {
  const { basis, equityPath, benchmarkPath, areaPath, latestEquity, latestBenchmark, yAxisTicks, xAxisTicks, plotBottom, plotLeft, plotRight } =
    useMemo(
      () => buildPerformanceChartModel(data, DEFAULT_PERFORMANCE_CHART_WIDTH, DEFAULT_PERFORMANCE_CHART_HEIGHT),
      [data],
    );
  // §C.6: the TWR line is drawn only when the payload publishes a TWR. A
  // suppressed cum_return next to a rising curve renders the same unpublishable
  // number as a shape, which is how a contaminated series still reads as +951%.
  const curveSuppressed = view.twrCumReturn == null;
  const showBenchmark = !curveSuppressed && view.benchmark != null && latestBenchmark != null;
  // Both lines carry the chart's basis: a base-100 TWR index, or dollar NAV on
  // a legacy payload. Labelling an index in dollars is how a deposit reads as
  // a gain.
  const isIndex = basis === "twr_index";
  const fmtChartValue = (value: number | null | undefined): string =>
    isIndex ? fmtRatio(value) : fmtUsdExact(value);
  const portfolioLabel = isIndex ? "Portfolio (TWR index)" : "Portfolio";
  const benchmarkLabel = `${view.benchmarkSymbol} ${isIndex ? "Indexed" : "Rebased"}`;

  return (
    <ChartPanel
      family="analytical-time-series"
      title="Equity Curve"
      badge={<span className="pill neutral">{data.series.length} SESSIONS</span>}
      legend={
        showBenchmark
          ? [
              { label: "Portfolio", role: "primary" as const },
              { label: `${view.benchmarkSymbol} rebased`, role: "comparison" as const },
            ]
          : [{ label: "Portfolio", role: "primary" as const }]
      }
      bodyClassName="performance-chart-shell"
      dataTestId="performance-chart-panel"
    >
      <svg
        data-testid="performance-equity-chart"
        viewBox={`0 0 ${DEFAULT_PERFORMANCE_CHART_WIDTH} ${DEFAULT_PERFORMANCE_CHART_HEIGHT}`}
        className="performance-chart"
        role="img"
        aria-label="Portfolio equity curve"
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
              className={isBaseline ? "performance-axis-line" : "performance-grid-line"}
            />
          );
        })}
        <g data-testid="performance-y-axis">
          <line
            x1={plotLeft}
            x2={plotLeft}
            y1={DEFAULT_PERFORMANCE_CHART_MARGINS.top}
            y2={plotBottom}
            className="performance-axis-line"
          />
          {yAxisTicks.map((tick) => (
            <g key={`y-${tick.value}`} className="performance-axis-tick">
              <line x1={plotLeft - 6} x2={plotLeft} y1={tick.y} y2={tick.y} className="performance-axis-line" />
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
        <path d={curveSuppressed ? "" : areaPath} fill="url(#performanceAreaGradient)" />
        {showBenchmark ? <path d={benchmarkPath} className="performance-line performance-line-benchmark" /> : null}
        <path
          d={curveSuppressed ? "" : equityPath}
          className="performance-line performance-line-equity"
          data-testid="performance-line-equity"
        />
        <g data-testid="performance-x-axis">
          <line x1={plotLeft} x2={plotRight} y1={plotBottom} y2={plotBottom} className="performance-axis-line" />
          {xAxisTicks.map((tick, index) => (
            <g key={`x-${tick.index}`} className="performance-axis-tick">
              <line x1={tick.x} x2={tick.x} y1={plotBottom} y2={plotBottom + 6} className="performance-axis-line" />
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
        {curveSuppressed ? (
          <div className="performance-meta-item" data-testid="performance-curve-suppressed">
            <span className="performance-meta-label">Curve suppressed</span>
            <span className="performance-meta-value">no publishable TWR</span>
          </div>
        ) : (
          <div className="performance-meta-item">
            <span className="performance-meta-label">{portfolioLabel}</span>
            <span className="performance-meta-value">{fmtChartValue(latestEquity)}</span>
          </div>
        )}
        {showBenchmark && view.benchmark ? (
          <>
            <div className="performance-meta-item">
              <span className="performance-meta-label">{benchmarkLabel}</span>
              <span className="performance-meta-value">{fmtChartValue(latestBenchmark)}</span>
            </div>
            <div className="performance-meta-item">
              <span className="performance-meta-label">Benchmark Return</span>
              <span className={`performance-meta-value ${toneClass(view.benchmark.benchmark_return)}`}>
                {fmtPct(view.benchmark.benchmark_return)}
              </span>
            </div>
          </>
        ) : null}
      </div>
    </ChartPanel>
  );
}

function buildCoreCards(view: PerformanceView): CardDef[] {
  const twrGate: GatedValue = {
    value: view.twrCumReturn,
    n: view.nReturns,
    min_n: TWR_GATES.MIN_N_CHAIN,
    unavailable_reason:
      view.twrCumReturn != null ? null : view.flowsStatus === "failed" ? "no_flow_data" : "not_computed",
  };

  return [
    toCard({
      id: "twr-total",
      label: "TWR Total",
      gate: twrGate,
      format: (value) => fmtPct(value),
      presentSub: `${fmtUsd(view.investmentPnl)} investment P&L`,
      definition:
        "Time-Weighted Return for the period. Sub-period returns are struck end-of-day with external flows removed, then chained geometrically, so deposits and withdrawals do not distort return.",
      formula:
        "r_t = (E_t - C_t - B_t) / B_t\nB_t = prior close NAV, E_t = close NAV, C_t = net external flow\nTWR = Π(1+r_t)-1\nInvestment P&L = ending equity - starting equity - net external flows",
    }),
    toCard({
      id: "annualized-twr",
      label: "Annualized TWR",
      gate: view.annualized,
      format: (value) => fmtPct(value),
      presentSub: `${view.calendarDays} CALENDAR DAYS`,
      definition:
        "Period TWR restated as a per-year rate on an Act/365 day count. A sub-year window is never annualized.",
      formula: "Annualized = (1+TWR)^(365/calendar_days)-1",
    }),
    toCard({
      id: "max-drawdown",
      label: "Max DD",
      gate: view.risk.max_drawdown,
      format: (value) => fmtPct(value),
      presentSub: `${view.drawdownDurationDays} DAYS`,
      definition: "Largest peak-to-trough decline of the TWR index. Measured on returns, never on dollar NAV.",
      formula: "Drawdown_t = (Index_t / Running Peak_t)-1\nMax DD = min Drawdown_t",
    }),
    toCard({
      id: "sharpe-vs-tbill",
      label: "Sharpe",
      gate: view.risk.sharpe_ratio,
      format: (value) => fmtRatio(value),
      presentSub: riskFreeCardCopy(view.methodology),
      definition:
        "Risk-adjusted return per unit of total volatility, measured as excess over the 3-month T-bill.",
      formula:
        "Sharpe = mean(r - rf_daily) / stdev(r) * sqrt(252)\nrf_daily = (1+rf_annual)^(1/252)-1",
    }),
  ];
}

/**
 * Beta / alpha / tracking error. A gated benchmark still renders its three
 * cards, dashed, carrying the reason the statistic is missing. They vanish only
 * when there is no benchmark series at all (§C.2); deleting them for every
 * failure mode is what made the benchmark gate copy unreachable on screen.
 */
function buildBenchmarkCards(view: PerformanceView): CardDef[] {
  const block = view.benchmark;
  if (!block && !view.hasBenchmarkSeries) return [];
  const symbol = view.benchmarkSymbol;
  const gateFor = (value: number | undefined): GatedValue =>
    block && value != null
      ? { value, n: block.n_common, min_n: TWR_GATES.MIN_N_BENCHMARK, unavailable_reason: null }
      : view.benchmarkGate;

  return [
    toCard({
      id: "beta",
      label: "Beta",
      gate: gateFor(block?.beta),
      format: (value) => fmtRatio(value),
      presentSub: symbol,
      tone: (value) => toneClass(value - 1),
      definition: `Sensitivity of portfolio daily returns to ${symbol} daily returns over the aligned sample.`,
      formula: `Beta = Cov(Portfolio, ${symbol}) / Var(${symbol})`,
    }),
    toCard({
      id: "alpha",
      label: "Alpha",
      gate: gateFor(block?.alpha_annualized),
      format: (value) => fmtPct(value),
      presentSub: "ANNUALIZED",
      definition: `Annualized excess return after adjusting for ${symbol} beta.`,
      formula: `Alpha = (mean(P) - Beta*mean(${symbol})) * 252`,
    }),
    toCard({
      id: "tracking-error",
      label: "Tracking Error",
      gate: gateFor(block?.tracking_error),
      format: (value) => fmtPctNoSign(value),
      presentSub: `TE vs ${symbol}`,
      tone: () => "neutral",
      definition: `Annualized standard deviation of active return versus ${symbol}.`,
      formula: `Active_t = P_t - ${symbol}_t\nTracking Error = stdev(Active) * sqrt(252)`,
    }),
  ];
}

function buildAdvancedCards(view: PerformanceView): CardDef[] {
  const cards: CardDef[] = [
    toCard({
      id: "mwr-irr",
      label: "MWR IRR",
      gate: view.mwrPeriod,
      format: (value) => fmtPct(value),
      presentSub: "DOLLAR EXPERIENCE",
      definition:
        "Money-Weighted Return over the whole window (Modified Dietz). Captures the dollar experience including the timing of deposits and withdrawals.",
      formula:
        "w_i = (D - (d_i - d_0).days) / D\nMWR = (E - B - ΣC_i) / (B + Σ w_i C_i)",
    }),
    ...buildBenchmarkCards(view),
  ];

  cards.push(
    toCard({
      id: "var-95",
      label: "VaR 95%",
      gate: view.risk.var_95,
      format: (value) => fmtPct(value),
      presentSub: view.risk.var_95.low_confidence ? "HISTORICAL / LOW CONFIDENCE" : "HISTORICAL",
      definition: "Historical 5th percentile of daily returns.",
      formula: "VaR_95 = quantile(daily_returns, 0.05)",
    }),
    toCard({
      id: "cvar-95",
      label: "CVaR 95%",
      gate: view.risk.cvar_95,
      format: (value) => fmtPct(value),
      presentSub: view.risk.cvar_95.low_confidence ? "SHORTFALL / LOW CONFIDENCE" : "EXPECTED SHORTFALL",
      definition: "Conditional VaR: mean return on days at or below VaR 95%.",
      formula: "CVaR_95 = mean(r | r ≤ VaR_95)",
    }),
  );

  return cards;
}

export default function PerformancePanel({ portfolioLastSync = null, marketState }: PerformancePanelProps) {
  const { isMobile, hasMounted } = useViewport();
  const isMarketActive = marketState !== MarketState.CLOSED;
  const { data, loading, error, syncNow } = usePerformance(isMarketActive);
  const view = useMemo(() => buildPerformanceView(data), [data]);
  const [activeCardId, setActiveCardId] = useState<string | null>(null);
  const requestedPortfolioSyncRef = useRef<string | null>(null);

  useEffect(() => {
    if (!data || !portfolioLastSync) return;
    if (!isPerformanceBehindPortfolioSync(data, portfolioLastSync)) return;
    if (requestedPortfolioSyncRef.current === portfolioLastSync) return;
    requestedPortfolioSyncRef.current = portfolioLastSync;
    syncNow();
  }, [data, portfolioLastSync, syncNow]);

  const coreCards = useMemo(() => (view ? buildCoreCards(view) : []), [view]);
  const advancedCards = useMemo(() => (view ? buildAdvancedCards(view) : []), [view]);
  const advancedGatedCount = useMemo(
    () => advancedCards.filter((card) => card.value === EMPTY).length,
    [advancedCards],
  );
  const allCards = useMemo(() => [...coreCards, ...advancedCards], [coreCards, advancedCards]);
  const activeCard = useMemo(() => allCards.find((c) => c.id === activeCardId) ?? null, [activeCardId, allCards]);

  if (hasMounted && isMobile) {
    return <MobilePerformancePanel portfolioLastSync={portfolioLastSync} marketState={marketState} />;
  }

  if (loading && !view) {
    return (
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Gauge size={14} />
            Performance
          </div>
          <span className="pill neutral">LOADING</span>
        </div>
        <div className="section-body performance-empty">
          <SpectralLoader label="Reconstructing TWR performance" />
        </div>
      </div>
    );
  }

  if (!data || !view) {
    return (
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <ShieldAlert size={14} />
            Performance
          </div>
          <span className="pill undefined">UNAVAILABLE</span>
        </div>
        <div className="section-body performance-empty">
          <SectionEmptyState
            icon={History}
            headline="No performance measurement yet"
            secondary="Performance is measured from Flex NAV. No snapshots are available for this period."
            testId="performance-empty"
          />
          {error ? <div className="performance-error-copy">{error}</div> : null}
        </div>
      </div>
    );
  }

  if (view.isInsufficient) {
    return (
      <div className="performance-panel" data-testid="performance-panel">
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <Activity size={14} />
              Performance
            </div>
            <span className="pill undefined">{view.status === "unavailable" ? "UNAVAILABLE" : "INSUFFICIENT DATA"}</span>
          </div>
          <div className="section-body">
            <SectionEmptyState
              icon={History}
              headline={
                view.status === "unavailable"
                  ? "No NAV series available"
                  : "Insufficient history for performance"
              }
              secondary="Performance is measured from IB Flex NAV. TWR starts at the first NAV date, not January 1, and needs at least two NAV observations."
              testId="performance-insufficient"
            />
            {view.warnings.length > 0 ? (
              <ul className="performance-note-list" data-testid="performance-insufficient-warnings">
                {view.warnings.map((w) => (
                  <li key={`${w.code}-${w.message}`}>{w.message}</li>
                ))}
              </ul>
            ) : null}
            <div className="performance-methodology-footer" data-testid="performance-methodology">
              <span className="performance-meta-label">Method</span>
              <span className="performance-meta-value">
                Time-weighted, beginning-of-day flow convention, chained geometrically
              </span>
              <span className="performance-meta-label">NAV as of</span>
              <span className="performance-meta-value">{view.navAsOf || EMPTY}</span>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const heroValue = fmtPct(view.twrCumReturn);
  // A suppressed branch chains nothing, so its return count is not a measured
  // zero. It reports no sample at all.
  const returnSessions = view.nReturns > 0 ? String(view.nReturns) : EMPTY;
  const bannerMessage = (view.errorWarnings[0] ?? view.warnings[0])?.message ?? "";
  const curveLabel = view.methodology.curve_type.replace(/_/g, " ");
  const basisLabel = view.methodology.return_basis.replace(/_/g, " ");
  const riskFreeCopy = riskFreeMethodologyCopy(view.methodology);
  const periodCopy = `${view.periodLabel} ${view.periodStart} to ${view.periodEnd}`;
  const sourcePillLabel =
    view.tradesSource === "ib_nav" ? "IB NAV" : view.tradesSource === "ib_flex" ? "IB FLEX" : view.tradesSource.toUpperCase();

  return (
    <div className="performance-panel" data-testid="performance-panel">
      <div className="section performance-hero">
        <div className="section-body performance-hero-body">
          <div>
            <div className="section-label-mono" data-testid="performance-period-label">
              {curveLabel.toUpperCase()} {periodCopy}
            </div>
            <div className="performance-hero-value">
              <span className={toneClass(view.twrCumReturn)} data-testid="performance-hero-twr">
                {heroValue}
              </span>
              <span className="performance-hero-unit"> TWR</span>
            </div>
            <div className="performance-hero-subtitle" data-testid="performance-hero-subtitle">
              Ending equity {fmtUsdExact(view.endingEquity)}
              {view.benchmark ? ` / ${view.benchmark.symbol} ${fmtPct(view.benchmark.benchmark_return)}` : ""} / as of{" "}
              {view.navAsOf} / N={returnSessions}
            </div>
          </div>
          <div className="performance-hero-pills">
            <span className="pill neutral" data-testid="performance-source-pill">
              {sourcePillLabel}
            </span>
            <span className="pill neutral">{returnSessions} SESSIONS</span>
            {view.risk.max_drawdown.value != null ? (
              <span className={`pill ${view.risk.max_drawdown.value < -0.1 ? "undefined" : "defined"}`}>
                MAX DD {fmtPct(view.risk.max_drawdown.value)}
              </span>
            ) : null}
          </div>
        </div>
      </div>

      {view.isDegraded ? (
        <div
          className="section performance-warnings"
          data-testid="performance-degraded-banner"
          style={{
            borderColor: "var(--negative)",
            background: "color-mix(in srgb, var(--negative) 8%, var(--bg-panel))",
          }}
        >
          <div
            className="section-header"
            style={{ borderBottomColor: "color-mix(in srgb, var(--negative) 24%, var(--line-grid))" }}
          >
            <div className="section-title" style={{ color: "var(--negative)" }}>
              <ShieldAlert size={14} />
              Degraded measurement
            </div>
            <span className="pill undefined">{view.warnings.length} FLAGS</span>
          </div>
          <div className="section-body performance-degraded-body">
            <div className="performance-degraded-headline">{bannerMessage}</div>
            <ul className="performance-note-list">
              {view.warnings.map((w) => (
                <li key={`${w.code}-${w.message}`}>
                  {w.code}: {w.message}
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}

      {view.isStale ? (
        <div
          className="section performance-warnings"
          data-testid="performance-stale-banner"
          style={{ borderColor: "var(--warn)", background: "color-mix(in srgb, var(--warn) 7%, var(--bg-panel))" }}
        >
          <div
            className="section-header"
            style={{ borderBottomColor: "color-mix(in srgb, var(--warn) 22%, var(--line-grid))" }}
          >
            <div className="section-title" style={{ color: "var(--warn-text)" }}>
              <AlertTriangle size={14} />
              Stale NAV
            </div>
            <span className="pill undefined">{view.navSource.toUpperCase().replace(/_/g, " ")}</span>
          </div>
          <div className="section-body performance-degraded-body">
            <div className="performance-degraded-headline">
              As of {view.navAsOf}, {view.navSessionsBehind} sessions behind the last completed session.
            </div>
            <ul className="performance-note-list">
              {view.warnings.map((w) => (
                <li key={`${w.code}-${w.message}`}>{w.message}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Gauge size={14} />
            Core Performance
          </div>
          <span className="pill defined">TWR</span>
        </div>
        <div className="section-body" style={{ padding: 0 }}>
          <div className="metrics-grid">
            {coreCards.map((card) => (
              <MetricCard key={card.id} card={card} onClick={() => setActiveCardId(card.id)} />
            ))}
          </div>
        </div>
      </div>

      <PerformanceChart data={data} view={view} />

      <div className="section" data-testid="performance-advanced">
        <div className="section-header">
          <div className="section-title">
            <TrendingDown size={14} />
            Advanced
          </div>
          {/* Reflect reality. This pill was hardcoded to "GATED" and sat above
              six populated metrics, which reads as a broken page. */}
          {advancedGatedCount > 0 ? (
            <span className="pill neutral">
              {advancedGatedCount} OF {advancedCards.length} GATED
            </span>
          ) : null}
        </div>
        <div className="section-body" style={{ padding: 0 }}>
          <div className="metrics-grid">
            {advancedCards.map((card) => (
              <MetricCard key={card.id} card={card} onClick={() => setActiveCardId(card.id)} />
            ))}
          </div>
        </div>
      </div>

      <div className="performance-grid-2">
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <TrendingDown size={14} />
              Path
            </div>
            <span className="pill neutral">DAILY</span>
          </div>
          <div className="section-body" style={{ padding: 0 }}>
            <div className="performance-metric-list">
              <div>
                <span>Current DD</span>
                <strong className={toneClass(view.risk.current_drawdown.value)}>
                  {fmtPct(view.risk.current_drawdown.value)}
                </strong>
              </div>
              <div>
                <span>Drawdown Trough</span>
                <strong>{view.drawdownTroughDate || EMPTY}</strong>
              </div>
              <div>
                <span>Return Sessions</span>
                <strong>{returnSessions}</strong>
              </div>
              <div>
                <span>Starting Equity</span>
                <strong>{fmtUsdExact(view.startingEquity)}</strong>
              </div>
            </div>
          </div>
        </div>

        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <History size={14} />
              Period
            </div>
            <span className="pill neutral">{view.navSource ? view.navSource.toUpperCase().replace(/_/g, " ") : "NAV"}</span>
          </div>
          <div className="section-body" style={{ padding: 0 }}>
            <div className="performance-metric-list">
              <div>
                <span>Period</span>
                <strong>
                  {view.periodStart} to {view.periodEnd}
                </strong>
              </div>
              <div>
                <span>External Flows</span>
                <strong>{fmtUsdExact(view.netExternalFlows)}</strong>
              </div>
              <div>
                <span>NAV As Of</span>
                <strong>{view.navAsOf || EMPTY}</strong>
              </div>
              <div>
                <span>Flows Status</span>
                <strong>{(view.flowsStatus || "unknown").replace(/_/g, " ")}</strong>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="section" data-testid="performance-methodology-footer">
        <div className="section-header">
          <div className="section-title">
            <AlertTriangle size={14} />
            Methodology
          </div>
          <span className="pill neutral">{basisLabel.toUpperCase()}</span>
        </div>
        <div className="section-body performance-meta-grid">
          <div className="performance-meta-item">
            <span className="performance-meta-label">Curve Type</span>
            <span className="performance-meta-value" data-testid="methodology-curve-type">
              {view.methodology.curve_type}
            </span>
          </div>
          <div className="performance-meta-item">
            <span className="performance-meta-label">Return Basis</span>
            <span className="performance-meta-value" data-testid="methodology-return-basis">
              {view.methodology.return_basis}
            </span>
          </div>
          <div className="performance-meta-item">
            <span className="performance-meta-label">Flow Convention</span>
            <span className="performance-meta-value">{view.methodology.flow_convention ?? "bod"}</span>
          </div>
          <div className="performance-meta-item">
            <span className="performance-meta-label">Risk-Free Rate (T-bill)</span>
            <span className="performance-meta-value" data-testid="methodology-rf">
              {riskFreeCopy}
            </span>
          </div>
          <div className="performance-meta-item">
            <span className="performance-meta-label">Stock History</span>
            <span className="performance-meta-value">{view.priceSources.stocks}</span>
          </div>
          <div className="performance-meta-item">
            <span className="performance-meta-label">Option History</span>
            <span className="performance-meta-value">{view.priceSources.options}</span>
          </div>
        </div>
        <div className="performance-methodology-copy">
          Time-weighted return, beginning-of-day flow convention, chained geometrically. External capital (deposits,
          withdrawals, ACATS and internal transfers) is removed from return; fees and borrow are returns, not flows.
          Volatility and ratios scale by {TWR_GATES.TRADING_DAYS} sessions; annualization uses an Act/
          {TWR_GATES.DAYS_PER_YEAR} day count. Any metric under its gate reports the reason it is missing rather than a
          number.
        </div>
      </div>

      {activeCard ? (
        <MetricDefinitionModal
          open
          title={activeCard.title}
          value={activeCard.value}
          definition={activeCard.definition}
          formula={activeCard.formula}
          onClose={() => setActiveCardId(null)}
        />
      ) : null}
    </div>
  );
}
