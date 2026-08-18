"use client";

import { Activity, AlertTriangle, ChevronDown, Gauge, History, ShieldAlert, TrendingDown } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { buildPerformanceChartModel } from "@/lib/performanceChart";
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
import ChartPanel from "../charts/ChartPanel";
import SectionEmptyState from "../SectionEmptyState";
import SpectralLoader from "../SpectralLoader";
import { BottomSheet } from "./BottomSheet";
import { Card } from "./Card";
import { CardRow } from "./CardRow";

const MOBILE_CHART_WIDTH = 390;
const MOBILE_CHART_HEIGHT = 220;
const MOBILE_CHART_MARGINS = {
  top: 14,
  right: 12,
  bottom: 32,
  left: 52,
} as const;

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

/** One card, one GatedValue, one copy generator. */
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
        "Time-Weighted Return for the period. Sub-period returns are struck end-of-day with external flows removed, then chained geometrically.",
      formula:
        "r_t = (E_t - C_t - B_t) / B_t\nTWR = Π(1+r_t)-1\nInvestment P&L = ending equity - starting equity - net external flows",
    }),
    toCard({
      id: "annualized-twr",
      label: "Annualized TWR",
      gate: view.annualized,
      format: (value) => fmtPct(value),
      presentSub: `${view.calendarDays} CALENDAR DAYS`,
      definition: "Period TWR restated as a per-year rate on an Act/365 day count. A sub-year window is never annualized.",
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
      definition: "Risk-adjusted return per unit of total volatility, measured as excess over the 3-month T-bill.",
      formula: "Sharpe = mean(r - rf_daily) / stdev(r) * sqrt(252)",
    }),
  ];
}

/** Dashed with its reason when the benchmark is gated; omitted only when there
 *  is no benchmark series at all (§C.2). Mirrors the desktop panel. */
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
        "Money-Weighted Return over the whole window (Modified Dietz). Captures the dollar experience including flow timing.",
      formula: "w_i = (D - (d_i - d_0).days) / D\nMWR = (E - B - ΣC_i) / (B + Σ w_i C_i)",
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
      formula: "CVaR_95 = mean of the worst 5% of sessions",
    }),
  );

  return cards;
}

function MobileStatCard({ card, onClick }: { card: CardDef; onClick: () => void }) {
  const cardTone = card.tone === "positive" ? "positive" : card.tone === "negative" ? "negative" : "default";
  return (
    <Card
      onClick={onClick}
      testId={`performance-card-${card.id}`}
      ariaLabel={`${card.label} metric details`}
      tone={cardTone}
      className="mobile-perf-card"
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            color: "var(--text-muted)",
          }}
        >
          {card.label}
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: "0.06em",
            color:
              card.tone === "positive"
                ? "var(--positive)"
                : card.tone === "negative"
                  ? "var(--negative)"
                  : "var(--text-secondary)",
          }}
          data-testid={`performance-sub-${card.id}`}
        >
          {card.sub}
        </span>
      </div>
      <div
        data-testid={`performance-value-${card.id}`}
        style={{
          fontFamily: "var(--font-sans)",
          fontSize: 22,
          fontWeight: 600,
          letterSpacing: "-0.02em",
          color:
            card.tone === "positive"
              ? "var(--positive)"
              : card.tone === "negative"
                ? "var(--negative)"
                : "var(--text-primary)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {card.value}
      </div>
    </Card>
  );
}

function MobilePerformanceChart({ data, view }: { data: PerformanceData; view: PerformanceView }) {
  const { basis, equityPath, benchmarkPath, areaPath, latestEquity, latestBenchmark, yAxisTicks, xAxisTicks, plotBottom, plotLeft, plotRight } =
    useMemo(
      () => buildPerformanceChartModel(data, MOBILE_CHART_WIDTH, MOBILE_CHART_HEIGHT, MOBILE_CHART_MARGINS),
      [data],
    );
  // §C.6: no publishable TWR, no line — see PerformancePanel for the rationale.
  const curveSuppressed = view.twrCumReturn == null;
  const showBenchmark = !curveSuppressed && view.benchmark != null && latestBenchmark != null;
  const isIndex = basis === "twr_index";
  const fmtChartValue = (value: number | null | undefined): string =>
    isIndex ? fmtRatio(value) : fmtUsdExact(value);

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
        viewBox={`0 0 ${MOBILE_CHART_WIDTH} ${MOBILE_CHART_HEIGHT}`}
        className="performance-chart"
        role="img"
        aria-label="Portfolio equity curve"
        style={{ width: "100%", height: "auto", display: "block" }}
      >
        <defs>
          <linearGradient id="mobilePerformanceAreaGradient" x1="0" y1="0" x2="0" y2="1">
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
          <line x1={plotLeft} x2={plotLeft} y1={MOBILE_CHART_MARGINS.top} y2={plotBottom} className="performance-axis-line" />
          {yAxisTicks.map((tick) => (
            <g key={`y-${tick.value}`} className="performance-axis-tick">
              <line x1={plotLeft - 4} x2={plotLeft} y1={tick.y} y2={tick.y} className="performance-axis-line" />
              <text
                x={plotLeft - 8}
                y={tick.y}
                textAnchor="end"
                dominantBaseline="middle"
                className="performance-axis-label"
                data-testid="performance-axis-y-label"
                style={{ fontSize: 9 }}
              >
                {tick.label}
              </text>
            </g>
          ))}
        </g>
        <path d={curveSuppressed ? "" : areaPath} fill="url(#mobilePerformanceAreaGradient)" />
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
              <line x1={tick.x} x2={tick.x} y1={plotBottom} y2={plotBottom + 4} className="performance-axis-line" />
              <text
                x={tick.x}
                y={plotBottom + 14}
                textAnchor={index === 0 ? "start" : index === xAxisTicks.length - 1 ? "end" : "middle"}
                className="performance-axis-label"
                data-testid="performance-axis-x-label"
                style={{ fontSize: 9 }}
              >
                {tick.label}
              </text>
            </g>
          ))}
        </g>
      </svg>
      <div className="performance-chart-meta" style={{ gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 10 }} data-testid="mobile-chart-meta">
        <div
          className="performance-meta-item"
          style={{ padding: "8px 10px" }}
          {...(curveSuppressed ? { "data-testid": "performance-curve-suppressed" } : {})}
        >
          <span className="performance-meta-label" style={{ fontSize: 9 }}>
            {curveSuppressed ? "Curve suppressed" : isIndex ? "Portfolio (TWR index)" : "Portfolio"}
          </span>
          <span className="performance-meta-value" style={{ fontSize: 12 }}>
            {curveSuppressed ? "no publishable TWR" : fmtChartValue(latestEquity)}
          </span>
        </div>
        {showBenchmark && view.benchmark ? (
          <>
            <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
              <span className="performance-meta-label" style={{ fontSize: 9 }}>
                {view.benchmarkSymbol} {isIndex ? "Indexed" : "Rebased"}
              </span>
              <span className="performance-meta-value" style={{ fontSize: 12 }}>
                {fmtChartValue(latestBenchmark)}
              </span>
            </div>
            <div className="performance-meta-item" style={{ padding: "8px 10px", gridColumn: "1 / -1" }}>
              <span className="performance-meta-label" style={{ fontSize: 9 }}>
                Benchmark Return
              </span>
              <span className={`performance-meta-value ${toneClass(view.benchmark.benchmark_return)}`} style={{ fontSize: 12 }}>
                {fmtPct(view.benchmark.benchmark_return)}
              </span>
            </div>
          </>
        ) : null}
      </div>
    </ChartPanel>
  );
}

type MobilePerformancePanelProps = {
  portfolioLastSync?: string | null;
  marketState?: MarketState;
};

export default function MobilePerformancePanel({ portfolioLastSync = null, marketState }: MobilePerformancePanelProps) {
  const isMarketActive = marketState !== MarketState.CLOSED;
  const { data, loading, error, syncNow } = usePerformance(isMarketActive);
  const view = useMemo(() => buildPerformanceView(data), [data]);
  const [activeCardId, setActiveCardId] = useState<string | null>(null);
  // Open by default: this section holds every gated metric AND the reason each
  // one is missing. A suppressed benchmark that states "SPY covers 83% of
  // sessions" behind a collapsed accordion is the mobile form of the same
  // unreachability that made the reasons dead copy in the first place.
  const [advancedOpen, setAdvancedOpen] = useState(true);
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

  if (loading && !view) {
    return (
      <div className="mobile-performance-panel performance-panel" data-testid="performance-panel" data-mobile="true">
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <Gauge size={14} />
              Performance
            </div>
            <span className="pill neutral">LOADING</span>
          </div>
          <div className="section-body" style={{ padding: 16 }}>
            <SpectralLoader label="Reconstructing TWR performance" />
          </div>
        </div>
      </div>
    );
  }

  if (!data || !view) {
    return (
      <div className="mobile-performance-panel performance-panel" data-testid="performance-panel" data-mobile="true">
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <ShieldAlert size={14} />
              Performance
            </div>
            <span className="pill undefined">UNAVAILABLE</span>
          </div>
          <div className="section-body" style={{ padding: 0 }}>
            <SectionEmptyState
              icon={History}
              headline="No performance measurement yet"
              secondary="Performance is measured from Flex NAV. No snapshots are available for this period."
              variant="compact"
              testId="performance-empty"
            />
            {error ? (
              <div style={{ padding: "10px 16px", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-secondary)" }}>
                {error}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  if (view.isInsufficient) {
    return (
      <div className="mobile-performance-panel performance-panel" data-testid="performance-panel" data-mobile="true" style={{ gap: 12 }}>
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <Activity size={14} />
              Performance
            </div>
            <span className="pill undefined">{view.status === "unavailable" ? "UNAVAILABLE" : "INSUFFICIENT DATA"}</span>
          </div>
          <div className="section-body" style={{ padding: 0 }}>
            <SectionEmptyState
              icon={History}
              headline={view.status === "unavailable" ? "No NAV series available" : "Insufficient history for performance"}
              secondary="Performance is measured from IB Flex NAV. TWR starts at the first NAV date, not January 1, and needs at least two NAV observations."
              variant="compact"
              testId="performance-insufficient"
            />
            {view.warnings.length > 0 ? (
              <ul
                data-testid="performance-insufficient-warnings"
                style={{ listStyle: "none", margin: 0, padding: "8px 12px", display: "flex", flexDirection: "column", gap: 6 }}
              >
                {view.warnings.map((w) => (
                  <li
                    key={`${w.code}-${w.message}`}
                    style={{
                      padding: "10px 12px",
                      border: "1px solid var(--border-dim)",
                      borderRadius: 4,
                      background: "var(--bg-panel-raised)",
                      fontFamily: "var(--font-mono)",
                      fontSize: 11,
                      lineHeight: 1.5,
                      color: "var(--text-secondary)",
                    }}
                  >
                    {w.message}
                  </li>
                ))}
              </ul>
            ) : null}
            <div
              data-testid="performance-methodology"
              style={{
                padding: "12px 16px",
                borderTop: "1px solid var(--border-dim)",
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                lineHeight: 1.6,
                color: "var(--text-muted)",
              }}
            >
              <div>Time-weighted, beginning-of-day flow convention, chained geometrically</div>
              <div style={{ marginTop: 4 }}>NAV as of {view.navAsOf || EMPTY}</div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const bannerMessage = (view.errorWarnings[0] ?? view.warnings[0])?.message ?? "";
  // A suppressed branch chains nothing; its return count is not a measured zero.
  const returnSessions = view.nReturns > 0 ? String(view.nReturns) : EMPTY;
  const curveLabel = view.methodology.curve_type.replace(/_/g, " ");
  const basisLabel = view.methodology.return_basis.replace(/_/g, " ");
  const riskFreeCopy = riskFreeMethodologyCopy(view.methodology);
  const periodCopy = `${view.periodLabel} ${view.periodStart} to ${view.periodEnd}`;
  const sourcePillLabel =
    view.tradesSource === "ib_nav" ? "IB NAV" : view.tradesSource === "ib_flex" ? "IB FLEX" : view.tradesSource.toUpperCase();

  return (
    <div className="mobile-performance-panel performance-panel" data-testid="performance-panel" data-mobile="true" style={{ gap: 12 }}>
      <div className="section performance-hero">
        <div className="section-body performance-hero-body" style={{ flexDirection: "column", alignItems: "stretch", padding: 16, gap: 12 }}>
          <div>
            <div className="section-label-mono" data-testid="performance-period-label" style={{ fontSize: 10 }}>
              {curveLabel.toUpperCase()} {periodCopy}
            </div>
            <div className="performance-hero-value" style={{ fontSize: 28, margin: "8px 0 6px" }}>
              <span className={toneClass(view.twrCumReturn)} data-testid="performance-hero-twr">
                {fmtPct(view.twrCumReturn)}
              </span>
              <span className="performance-hero-unit" style={{ fontSize: 12, marginLeft: 6, color: "var(--text-muted)" }}>
                TWR
              </span>
            </div>
            <div className="performance-hero-subtitle" data-testid="performance-hero-subtitle" style={{ fontSize: 10, lineHeight: 1.5 }}>
              Ending equity {fmtUsdExact(view.endingEquity)}
              {view.benchmark ? ` / ${view.benchmark.symbol} ${fmtPct(view.benchmark.benchmark_return)}` : ""} / as of{" "}
              {view.navAsOf} / N={returnSessions}
            </div>
          </div>
          <div className="performance-hero-pills" style={{ justifyContent: "flex-start", gap: 6 }}>
            <span className="pill neutral" data-testid="performance-source-pill" style={{ fontSize: 10 }}>
              {sourcePillLabel}
            </span>
            <span className="pill neutral" style={{ fontSize: 10 }}>
              {returnSessions} SESSIONS
            </span>
            {view.risk.max_drawdown.value != null ? (
              <span className={`pill ${view.risk.max_drawdown.value < -0.1 ? "undefined" : "defined"}`} style={{ fontSize: 10 }}>
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
          style={{ borderColor: "var(--negative)", background: "color-mix(in srgb, var(--negative) 8%, var(--bg-panel))" }}
        >
          <div className="section-header" style={{ borderBottomColor: "color-mix(in srgb, var(--negative) 24%, var(--line-grid))" }}>
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
          <div className="section-header" style={{ borderBottomColor: "color-mix(in srgb, var(--warn) 22%, var(--line-grid))" }}>
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
        <div className="section-body" style={{ padding: 8 }}>
          <div className="mobile-card-list" data-testid="mobile-metrics-stack">
            {coreCards.map((card) => (
              <MobileStatCard key={card.id} card={card} onClick={() => setActiveCardId(card.id)} />
            ))}
          </div>
        </div>
      </div>

      <div style={{ margin: 0 }}>
        <MobilePerformanceChart data={data} view={view} />
      </div>

      <div className="section" data-testid="performance-advanced">
        <button
          type="button"
          data-testid="mobile-advanced-toggle"
          aria-expanded={advancedOpen}
          onClick={() => setAdvancedOpen((v) => !v)}
          style={{
            width: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "12px 16px",
            background: "var(--bg-panel)",
            border: "none",
            borderBottom: advancedOpen ? "1px solid var(--border-dim)" : "none",
            cursor: "pointer",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: "var(--text-secondary)",
            textAlign: "left",
          }}
        >
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <TrendingDown size={12} />
            Advanced
            <span className="pill neutral" style={{ fontSize: 9, marginLeft: 4 }} data-testid="mobile-advanced-count">
              {advancedOpen ? "OPEN" : "COLLAPSED"}
            </span>
            {advancedGatedCount > 0 ? (
              <span className="pill neutral" style={{ fontSize: 9 }}>
                {advancedGatedCount} OF {advancedCards.length} GATED
              </span>
            ) : null}
          </span>
          <ChevronDown
            size={14}
            style={{
              transform: advancedOpen ? "rotate(180deg)" : "rotate(0deg)",
              transition: "transform 180ms ease",
              color: "var(--text-muted)",
              flexShrink: 0,
            }}
          />
        </button>

        {advancedOpen && (
          <div data-testid="mobile-advanced-content">
            <div className="mobile-card-list" style={{ padding: 8 }}>
              {advancedCards.map((card) => (
                <MobileStatCard key={card.id} card={card} onClick={() => setActiveCardId(card.id)} />
              ))}
            </div>

            <div style={{ borderTop: "1px solid var(--border-dim)", display: "flex", flexDirection: "column", gap: 0 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "12px 16px 8px",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  color: "var(--text-muted)",
                }}
              >
                <TrendingDown size={12} />
                Path
                <span className="pill neutral" style={{ fontSize: 9, marginLeft: "auto" }}>
                  DAILY
                </span>
              </div>
              <div style={{ padding: "0 8px 8px", display: "flex", flexDirection: "column", gap: 6 }}>
                <CardRow
                  label="Current DD"
                  value={fmtPct(view.risk.current_drawdown.value)}
                  align="compact"
                  tone={(view.risk.current_drawdown.value ?? 0) < 0 ? "negative" : "default"}
                />
                <CardRow label="Drawdown Trough" value={view.drawdownTroughDate || EMPTY} align="compact" />
                <CardRow label="Return Sessions" value={returnSessions} align="compact" />
                <CardRow label="Starting Equity" value={fmtUsdExact(view.startingEquity)} align="compact" />
              </div>
            </div>

            <div style={{ borderTop: "1px solid var(--border-dim)", display: "flex", flexDirection: "column", gap: 0 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "12px 16px 8px",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  color: "var(--text-muted)",
                }}
              >
                <History size={12} />
                Period
                <span className="pill neutral" style={{ fontSize: 9, marginLeft: "auto" }}>
                  {view.navSource ? view.navSource.toUpperCase().replace(/_/g, " ") : "NAV"}
                </span>
              </div>
              <div style={{ padding: "0 8px 8px", display: "flex", flexDirection: "column", gap: 6 }}>
                <CardRow label="Period" value={`${view.periodStart} to ${view.periodEnd}`} align="compact" />
                <CardRow label="External Flows" value={fmtUsdExact(view.netExternalFlows)} align="compact" />
                <CardRow label="NAV As Of" value={view.navAsOf || EMPTY} align="compact" />
                <CardRow label="Flows Status" value={(view.flowsStatus || "unknown").replace(/_/g, " ")} align="compact" />
              </div>
            </div>

            <div data-testid="performance-methodology-footer" style={{ borderTop: "1px solid var(--border-dim)", padding: 12 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  color: "var(--text-muted)",
                  marginBottom: 10,
                }}
              >
                <AlertTriangle size={12} />
                Methodology
                <span className="pill neutral" style={{ fontSize: 9, marginLeft: "auto" }}>
                  {basisLabel.toUpperCase()}
                </span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
                  <span className="performance-meta-label" style={{ fontSize: 9 }}>
                    Curve Type
                  </span>
                  <span className="performance-meta-value" data-testid="methodology-curve-type" style={{ fontSize: 11 }}>
                    {view.methodology.curve_type}
                  </span>
                </div>
                <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
                  <span className="performance-meta-label" style={{ fontSize: 9 }}>
                    Return Basis
                  </span>
                  <span className="performance-meta-value" data-testid="methodology-return-basis" style={{ fontSize: 11 }}>
                    {view.methodology.return_basis}
                  </span>
                </div>
                <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
                  <span className="performance-meta-label" style={{ fontSize: 9 }}>
                    Flow Convention
                  </span>
                  <span className="performance-meta-value" style={{ fontSize: 11 }}>
                    {view.methodology.flow_convention ?? "bod"}
                  </span>
                </div>
                <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
                  <span className="performance-meta-label" style={{ fontSize: 9 }}>
                    Risk-Free Rate (T-bill)
                  </span>
                  <span className="performance-meta-value" data-testid="methodology-rf" style={{ fontSize: 11 }}>
                    {riskFreeCopy}
                  </span>
                </div>
                <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
                  <span className="performance-meta-label" style={{ fontSize: 9 }}>
                    Stock History
                  </span>
                  <span className="performance-meta-value" style={{ fontSize: 11 }}>
                    {view.priceSources.stocks}
                  </span>
                </div>
                <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
                  <span className="performance-meta-label" style={{ fontSize: 9 }}>
                    Option History
                  </span>
                  <span className="performance-meta-value" style={{ fontSize: 11 }}>
                    {view.priceSources.options}
                  </span>
                </div>
              </div>
              <div
                style={{
                  marginTop: 10,
                  padding: "10px 12px",
                  border: "1px solid var(--border-dim)",
                  borderRadius: 4,
                  background: "var(--bg-panel-raised)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  lineHeight: 1.6,
                  letterSpacing: "0.04em",
                  color: "var(--text-muted)",
                }}
              >
                Time-weighted return, beginning-of-day flow convention, chained geometrically. External capital is removed
                from return; fees and borrow are returns, not flows. Volatility and ratios scale by{" "}
                {TWR_GATES.TRADING_DAYS} sessions; annualization uses an Act/{TWR_GATES.DAYS_PER_YEAR} day count.
              </div>
            </div>
          </div>
        )}
      </div>

      <BottomSheet open={Boolean(activeCard)} onClose={() => setActiveCardId(null)} title={activeCard?.title} testId="mobile-metric-definition">
        {activeCard ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 14, padding: "4px 0 8px" }}>
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 22,
                fontWeight: 600,
                color: "var(--text-primary)",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {activeCard.value}
            </div>
            <div>
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  color: "var(--text-muted)",
                  marginBottom: 6,
                }}
              >
                What It Is
              </div>
              <p style={{ margin: 0, fontFamily: "var(--font-sans)", fontSize: 13, lineHeight: 1.5, color: "var(--text-secondary)" }}>
                {activeCard.definition}
              </p>
            </div>
            <div>
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  color: "var(--text-muted)",
                  marginBottom: 6,
                }}
              >
                How It Is Calculated
              </div>
              <div style={{ background: "var(--bg-panel-raised)", border: "1px solid var(--border-dim)", borderRadius: 4, padding: "10px 12px" }}>
                <code style={{ fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.6, whiteSpace: "pre-wrap", color: "var(--text-primary)" }}>
                  {activeCard.formula}
                </code>
              </div>
            </div>
            {activeCard.value === EMPTY ? (
              <div
                style={{
                  padding: "8px 12px",
                  border: "1px solid var(--border-dim)",
                  borderRadius: 4,
                  background: "color-mix(in srgb, var(--warn) 8%, var(--bg-panel-raised))",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  color: "var(--text-muted)",
                }}
              >
                {activeCard.sub}
              </div>
            ) : null}
          </div>
        ) : null}
      </BottomSheet>
    </div>
  );
}
