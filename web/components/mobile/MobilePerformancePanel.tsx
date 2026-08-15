"use client";

import { Activity, AlertTriangle, ChevronDown, Gauge, History, ShieldAlert, TrendingDown } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { buildPerformanceChartModel, resolvePerformanceStartingEquity } from "@/lib/performanceChart";
import { normalizePerformanceData } from "@/lib/performanceData";
import { isPerformanceBehindPortfolioSync } from "@/lib/performanceFreshness";
import type { PerformanceData, PerformanceSeriesPoint } from "@/lib/types";
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

function fmtUsd(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${value < 0 ? "-" : ""}$${(abs / 1_000_000).toFixed(2)}M`;
  return `${value < 0 ? "-" : ""}$${abs.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

function fmtUsdExact(value: number): string {
  return `${value < 0 ? "-" : ""}$${Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtPct(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
}

function fmtPctNoSign(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

function fmtRatio(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toFixed(2);
}

function toneClass(value: number | null | undefined): "positive" | "negative" | "neutral" {
  if (value == null || !Number.isFinite(value)) return "neutral";
  return value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
}

function isInsufficientData(data: PerformanceData): boolean {
  const d = data as unknown as Record<string, unknown>;
  if (d["status"] === "insufficient_data") return true;
  if (d["status"] === "insufficient") return true;
  if (data.series.length < 2) return true;
  if (data.summary.trading_days < 2) return true;
  const hasInsufficientWarning = data.warnings.some((w) => /insufficient|no flex nav|add.*flex/i.test(w));
  if (hasInsufficientWarning && data.series.length < 2) return true;
  return false;
}

function drawdownLeader(series: PerformanceSeriesPoint[]): string {
  if (series.length === 0) return "—";
  const worst = series.reduce((acc, point) => (point.drawdown < acc.drawdown ? point : acc), series[0]);
  return worst?.date ?? "—";
}

type CardDef = {
  id: string;
  label: string;
  title: string;
  value: string;
  sub: string;
  tone: "positive" | "negative" | "neutral";
  gated: boolean;
  gateLabel?: string;
  definition: string;
  formula: string;
};

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
      {card.gated ? (
        <div
          data-testid={`performance-gate-${card.id}`}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            letterSpacing: "0.04em",
            color: "var(--text-muted)",
          }}
        >
          {card.gateLabel}
        </div>
      ) : null}
    </Card>
  );
}

function MobilePerformanceChart({ data }: { data: PerformanceData }) {
  const { equityPath, benchmarkPath, areaPath, latestEquity, latestBenchmark, yAxisTicks, xAxisTicks, plotBottom, plotLeft, plotRight } =
    useMemo(
      () => buildPerformanceChartModel(data, MOBILE_CHART_WIDTH, MOBILE_CHART_HEIGHT, MOBILE_CHART_MARGINS),
      [data],
    );

  return (
    <ChartPanel
      family="analytical-time-series"
      title="Equity Curve"
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
        viewBox={`0 0 ${MOBILE_CHART_WIDTH} ${MOBILE_CHART_HEIGHT}`}
        className="performance-chart"
        role="img"
        aria-label="Portfolio equity curve versus benchmark"
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
        <path d={areaPath} fill="url(#mobilePerformanceAreaGradient)" />
        <path d={benchmarkPath} className="performance-line performance-line-benchmark" />
        <path d={equityPath} className="performance-line performance-line-equity" />
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
        <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
          <span className="performance-meta-label" style={{ fontSize: 9 }}>
            Portfolio
          </span>
          <span className="performance-meta-value" style={{ fontSize: 12 }}>
            {fmtUsdExact(latestEquity)}
          </span>
        </div>
        <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
          <span className="performance-meta-label" style={{ fontSize: 9 }}>
            {data.benchmark} Rebased
          </span>
          <span className="performance-meta-value" style={{ fontSize: 12 }}>
            {fmtUsdExact(latestBenchmark)}
          </span>
        </div>
        <div className="performance-meta-item" style={{ padding: "8px 10px", gridColumn: "1 / -1" }}>
          <span className="performance-meta-label" style={{ fontSize: 9 }}>
            Benchmark Return
          </span>
          <span className={`performance-meta-value ${toneClass(data.benchmark_total_return)}`} style={{ fontSize: 12 }}>
            {fmtPct(data.benchmark_total_return)}
          </span>
        </div>
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
  const { data: raw, loading, error, syncNow } = usePerformance(isMarketActive);
  const data = useMemo(() => normalizePerformanceData(raw), [raw]);
  const [activeCardId, setActiveCardId] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const requestedPortfolioSyncRef = useRef<string | null>(null);

  useEffect(() => {
    if (!data || !portfolioLastSync) return;
    if (!isPerformanceBehindPortfolioSync(data, portfolioLastSync)) return;
    if (requestedPortfolioSyncRef.current === portfolioLastSync) return;
    requestedPortfolioSyncRef.current = portfolioLastSync;
    syncNow();
  }, [data, portfolioLastSync, syncNow]);

  const coreCards: CardDef[] = useMemo(() => {
    if (!data) return [];
    const { summary } = data;
    const N = summary.trading_days;
    const hasAnnualized = N >= 20 && summary.annualized_return != null && Number.isFinite(summary.annualized_return);
    const sharpeGated = N >= 20;
    const rfLabel =
      data.methodology.risk_free_rate > 0
        ? `RF ${(data.methodology.risk_free_rate * 100).toFixed(2)}% DGS3MO`
        : "RF 0.00% fallback";
    return [
      {
        id: "twr-total",
        label: "TWR Total",
        title: "TWR Total",
        value: fmtPct(summary.total_return),
        sub: `${fmtUsd(summary.pnl)} P&L`,
        tone: toneClass(summary.total_return),
        gated: false,
        definition:
          "Time-Weighted Return for the period. Chain-linked Modified Dietz per sub-period so deposits and withdrawals do not distort return. TWR_cum = \u03A0(1+r_i)-1.",
        formula: "r_i = (E - B - C) / B\nB = prior close NAV, E = close NAV, C = external flows on date\nTWR = \u03A0(1+r_i)-1\nP&L = ending equity - starting equity",
      },
      {
        id: "annualized-twr",
        label: "Annualized TWR",
        title: "Annualized TWR",
        value: hasAnnualized ? fmtPct(summary.annualized_return) : "—",
        sub: hasAnnualized ? `${N} SESSIONS` : `needs 20 sessions (N=${N})`,
        tone: hasAnnualized ? toneClass(summary.annualized_return) : "neutral",
        gated: !hasAnnualized,
        gateLabel: !hasAnnualized ? `Annualized requires N \u2265 20` : undefined,
        definition:
          "Annualized TWR extrapolates the period TWR to a 252-session year. Only shown when the sample is large enough to be meaningful.",
        formula: "Annualized = (1+TWR)^(252/N)-1\nN = trading sessions in period\nShown only when N \u2265 20, otherwise \u2014",
      },
      {
        id: "max-drawdown",
        label: "Max DD",
        title: "Max Drawdown",
        value: fmtPct(summary.max_drawdown),
        sub: `${summary.max_drawdown_duration_days} DAYS`,
        tone: toneClass(summary.max_drawdown),
        gated: false,
        definition: "Largest peak-to-trough decline in the TWR-consistent equity curve. Measures the worst capital drawdown in the period.",
        formula: "Drawdown_t = (Equity_t / Running Peak_t)-1\nMax DD = min Drawdown_t\nDuration = sessions below prior peak",
      },
      {
        id: "sharpe-vs-tbill",
        label: "Sharpe vs T-bill",
        title: "Sharpe vs T-bill",
        value: sharpeGated && Number.isFinite(summary.sharpe_ratio) ? fmtRatio(summary.sharpe_ratio) : "—",
        sub: sharpeGated ? rfLabel : `needs 20 sessions (N=${N})`,
        tone: sharpeGated ? toneClass(summary.sharpe_ratio) : "neutral",
        gated: !sharpeGated,
        gateLabel: !sharpeGated ? "Sharpe requires N \u2265 20" : undefined,
        definition:
          "Risk-adjusted return per unit of total volatility, measured as excess over the 3-month T-bill (FRED DGS3MO). Higher values mean more return for each unit of realized volatility.",
        formula:
          "Sharpe = mean(daily - Rf/252) / stdev(daily) * sqrt(252)\nRf = FRED DGS3MO daily series, fallback 0 with warning\nVol = stdev(daily) * sqrt(252)\nShown only when N \u2265 20, otherwise \u2014",
      },
    ];
  }, [data]);

  const advancedCards: CardDef[] = useMemo(() => {
    if (!data) return [];
    const { summary } = data;
    const N = summary.trading_days;
    const raw = data as unknown as Record<string, unknown>;
    const summaryRaw = summary as unknown as Record<string, unknown>;
    const mwrValue = (summaryRaw["mwr_irr"] as number | null) ?? (raw["mwr_irr"] as number | null) ?? null;
    const hasMwr = N >= 20 && mwrValue != null && Number.isFinite(mwrValue);
    const hasBeta = N >= 40 && summary.beta != null && Number.isFinite(summary.beta);
    const hasAlpha = N >= 40 && summary.alpha != null && Number.isFinite(summary.alpha);
    const hasTracking = N >= 40 && summary.tracking_error != null && Number.isFinite(summary.tracking_error);
    const hasVar = N >= 60 && summary.var_95 != null && Number.isFinite(summary.var_95);
    const hasCvar = N >= 60 && summary.cvar_95 != null && Number.isFinite(summary.cvar_95);
    return [
      {
        id: "mwr-irr",
        label: "MWR IRR",
        title: "MWR IRR",
        value: hasMwr ? fmtPct(mwrValue) : "—",
        sub: hasMwr ? "DOLLAR EXPERIENCE" : `needs 20 sessions (N=${N})`,
        tone: hasMwr ? toneClass(mwrValue) : "neutral",
        gated: !hasMwr,
        gateLabel: !hasMwr ? "MWR requires N \u2265 20" : undefined,
        definition:
          "Money-Weighted Return (IRR) captures the dollar experience including the timing of deposits and withdrawals. Compare to TWR to see timing impact.",
        formula: "MWR solves NPV=0 for dated external flows C_t plus ending equity\nShown only when N \u2265 20, otherwise \u2014",
      },
      {
        id: "beta",
        label: "Beta",
        title: "Beta",
        value: hasBeta ? fmtRatio(summary.beta) : "—",
        sub: hasBeta ? data.benchmark : `needs 40 sessions (N=${N})`,
        tone: hasBeta ? toneClass(summary.beta - 1) : "neutral",
        gated: !hasBeta,
        gateLabel: !hasBeta ? "Beta requires N \u2265 40" : undefined,
        definition: `Sensitivity of portfolio daily returns to ${data.benchmark} daily returns. Beta above 1 implies amplified market sensitivity.`,
        formula: `Beta = Cov(Portfolio, ${data.benchmark}) / Var(${data.benchmark})\nShown only when N \u2265 40, otherwise \u2014`,
      },
      {
        id: "alpha",
        label: "Alpha",
        title: "Alpha",
        value: hasAlpha ? fmtPct(summary.alpha) : "—",
        sub: hasAlpha ? "ANNUALIZED" : `needs 40 sessions (N=${N})`,
        tone: hasAlpha ? toneClass(summary.alpha) : "neutral",
        gated: !hasAlpha,
        gateLabel: !hasAlpha ? "Alpha requires N \u2265 40" : undefined,
        definition: `Annualized excess return after adjusting for ${data.benchmark} beta. Positive alpha means outperformance beyond market exposure.`,
        formula: `Alpha = (mean(P) - Beta*mean(${data.benchmark}))*252\nShown only when N \u2265 40, otherwise \u2014`,
      },
      {
        id: "tracking-error",
        label: "Tracking Error",
        title: "Tracking Error",
        value: hasTracking ? fmtPctNoSign(summary.tracking_error) : "—",
        sub: hasTracking ? `TE vs ${data.benchmark}` : `needs 40 sessions (N=${N})`,
        tone: "neutral",
        gated: !hasTracking,
        gateLabel: !hasTracking ? "Tracking Error requires N \u2265 40" : undefined,
        definition: `Annualized standard deviation of active return versus ${data.benchmark}. Measures benchmark-relative volatility.`,
        formula: `Active_t = P_t - ${data.benchmark}_t\nTracking Error = stdev(Active)*sqrt(252)\nShown only when N \u2265 40, otherwise \u2014`,
      },
      {
        id: "var-95",
        label: "VaR 95%",
        title: "VaR 95%",
        value: hasVar ? fmtPct(summary.var_95) : "—",
        sub: hasVar ? "HISTORICAL" : `needs 60 sessions (N=${N})`,
        tone: hasVar ? toneClass(summary.var_95) : "neutral",
        gated: !hasVar,
        gateLabel: !hasVar ? "VaR requires N \u2265 60" : undefined,
        definition: "Historical 5th percentile of daily returns. The loss threshold that was exceeded on 5% of days.",
        formula: "VaR_95 = quantile(daily_returns, 0.05)\nShown only when N \u2265 60, otherwise \u2014",
      },
      {
        id: "cvar-95",
        label: "CVaR 95%",
        title: "CVaR 95%",
        value: hasCvar ? fmtPct(summary.cvar_95) : "—",
        sub: hasCvar ? "EXPECTED SHORTFALL" : `needs 60 sessions (N=${N})`,
        tone: hasCvar ? toneClass(summary.cvar_95) : "neutral",
        gated: !hasCvar,
        gateLabel: !hasCvar ? "CVaR requires N \u2265 60" : undefined,
        definition: "Conditional VaR: mean return on days at or below VaR 95%. Expected loss when the tail event occurs.",
        formula: "CVaR_95 = mean(r | r \u2264 VaR_95)\nShown only when N \u2265 60, otherwise \u2014",
      },
    ];
  }, [data]);

  const allCards = useMemo(() => [...coreCards, ...advancedCards], [coreCards, advancedCards]);
  const activeCard = useMemo(() => allCards.find((c) => c.id === activeCardId) ?? null, [activeCardId, allCards]);

  if (loading && !data) {
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

  if (!data) {
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
              <div
                style={{
                  padding: "10px 16px",
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--text-secondary)",
                }}
              >
                {error}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  if (isInsufficientData(data)) {
    const insufficientWarnings = data.warnings.filter(Boolean);
    return (
      <div className="mobile-performance-panel performance-panel" data-testid="performance-panel" data-mobile="true" style={{ gap: 12 }}>
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <Activity size={14} />
              Performance
            </div>
            <span className="pill undefined">INSUFFICIENT DATA</span>
          </div>
          <div className="section-body" style={{ padding: 0 }}>
            <SectionEmptyState
              icon={History}
              headline="Insufficient history for performance"
              secondary="Performance needs Flex NAV from IB. Add the EquitySummary report in IB Portal as IB_FLEX_NAV_QUERY_ID and run a Flex fetch. TWR starts at the first NAV date, not January 1, and requires at least two NAV snapshots."
              variant="compact"
              testId="performance-insufficient"
            />
            {insufficientWarnings.length > 0 ? (
              <ul
                data-testid="performance-insufficient-warnings"
                style={{
                  listStyle: "none",
                  margin: 0,
                  padding: "8px 12px",
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                }}
              >
                {insufficientWarnings.map((w) => (
                  <li
                    key={w}
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
                    {w}
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
              <div>TWR Modified Dietz daily, chained geometrically</div>
              <div style={{ marginTop: 4 }}>After Flex settles, around 06:00 ET. Today returns are not fabricated from intraday marks.</div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const { summary } = data;
  const N = summary.trading_days;
  const startingEquity = resolvePerformanceStartingEquity(data);
  const externalFlowWarnings = data.warnings.filter((w) => /deposit|withdrawal|flow|acats|transfer/i.test(w));
  const otherWarnings = data.warnings.filter((w) => !/deposit|withdrawal|flow|acats|transfer/i.test(w));
  const hasWarnings = data.warnings.length > 0 || data.contracts_missing_history.length > 0;
  const troughDate = drawdownLeader(data.series);
  const rfPct = fmtPctNoSign(data.methodology.risk_free_rate, 2);
  const curveLabel = data.methodology.curve_type === "ib_daily_nav" ? "IB NAV" : data.methodology.curve_type.replace(/_/g, " ");
  const basisLabel = data.methodology.return_basis.replace(/_/g, " ");
  const periodCopy =
    data.period_label && data.period_start && data.period_end
      ? `${data.period_label} ${data.period_start} to ${data.period_end}`
      : data.period_label || `${data.period_start} to ${data.period_end}`;

  return (
    <div className="mobile-performance-panel performance-panel" data-testid="performance-panel" data-mobile="true" style={{ gap: 12 }}>
      {/* Hero — stacked vertically for mobile thumb zone */}
      <div className="section performance-hero">
        <div className="section-body performance-hero-body" style={{ flexDirection: "column", alignItems: "stretch", padding: 16, gap: 12 }}>
          <div>
            <div className="section-label-mono" data-testid="performance-period-label" style={{ fontSize: 10 }}>
              {curveLabel.toUpperCase()} {periodCopy}
            </div>
            <div className="performance-hero-value" style={{ fontSize: 28, margin: "8px 0 6px" }}>
              <span className={toneClass(summary.total_return)} data-testid="performance-hero-twr">
                {fmtPct(summary.total_return)}
              </span>
              <span className="performance-hero-unit" style={{ fontSize: 12, marginLeft: 6, color: "var(--text-muted)" }}>
                TWR
              </span>
            </div>
            <div className="performance-hero-subtitle" data-testid="performance-hero-subtitle" style={{ fontSize: 10, lineHeight: 1.5 }}>
              Ending equity {fmtUsdExact(summary.ending_equity)} / {data.benchmark} {fmtPct(data.benchmark_total_return)} / as of {data.as_of} / N={N}
            </div>
          </div>
          <div className="performance-hero-pills" style={{ justifyContent: "flex-start", gap: 6 }}>
            <span className="pill neutral" data-testid="performance-source-pill" style={{ fontSize: 10 }}>
              {data.trades_source === "ib_nav" ? "IB NAV" : data.trades_source === "ib_flex" ? "IB FLEX" : data.trades_source.toUpperCase()}
            </span>
            <span className="pill neutral" style={{ fontSize: 10 }}>
              {N} DAYS
            </span>
            <span className={`pill ${summary.max_drawdown < -0.1 ? "undefined" : "defined"}`} style={{ fontSize: 10 }}>
              MAX DD {fmtPct(summary.max_drawdown)}
            </span>
          </div>
        </div>
      </div>

      {/* Warnings / external flows banner — adapted compact */}
      {hasWarnings ? (
        <div
          className="section performance-warnings"
          data-testid="performance-warnings"
          style={{ borderColor: "var(--warn)", background: "color-mix(in srgb, var(--warn) 7%, var(--bg-panel))" }}
        >
          <div className="section-header" style={{ borderBottomColor: "color-mix(in srgb, var(--warn) 22%, var(--line-grid))" }}>
            <div className="section-title" style={{ color: "var(--warn-text)" }}>
              <AlertTriangle size={14} />
              Flows and Data Notes
            </div>
            <span className="pill undefined" data-testid="performance-warnings-count">
              {data.warnings.length} FLAGS
            </span>
          </div>
          <div className="section-body" style={{ padding: 0 }}>
            {externalFlowWarnings.length > 0 ? (
              <div
                data-testid="performance-flows-banner"
                style={{
                  padding: "10px 12px",
                  fontFamily: "var(--font-mono)",
                  fontSize: "11px",
                  letterSpacing: "0.04em",
                  color: "var(--text-secondary)",
                  borderBottom: "1px solid color-mix(in srgb, var(--warn) 18%, var(--line-grid))",
                  background: "color-mix(in srgb, var(--warn) 9%, transparent)",
                  lineHeight: 1.5,
                }}
              >
                {externalFlowWarnings.map((w) => (
                  <div key={w}>{w}</div>
                ))}
                <div style={{ marginTop: 4, color: "var(--text-muted)", fontSize: 10 }}>External flows are excluded from TWR.</div>
              </div>
            ) : null}
            {otherWarnings.length > 0 || data.contracts_missing_history.length > 0 ? (
              <ul className="performance-note-list" style={{ padding: "4px 0" }}>
                {otherWarnings.map((w) => (
                  <li key={w} style={{ padding: "8px 12px", fontSize: 11 }}>
                    {w}
                  </li>
                ))}
                {data.contracts_missing_history.length > 0 ? (
                  <li data-testid="performance-missing-history" style={{ padding: "8px 12px", fontSize: 11 }}>
                    {data.contracts_missing_history.length} contract(s) were missing historical marks and were marked to zero where no price history was available.
                  </li>
                ) : null}
              </ul>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* Core performance — stacked cards full-width */}
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

      {/* Chart — full-width, no side inset */}
      <div style={{ margin: 0 }}>
        <MobilePerformanceChart data={data} />
      </div>

      {/* Advanced — collapsible section */}
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
            <span className="pill neutral" style={{ fontSize: 9 }}>
              GATED BY N
            </span>
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
            <div
              style={{
                padding: "10px 16px",
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                color: "var(--text-muted)",
                borderTop: "1px solid var(--border-dim)",
                background: "var(--bg-panel)",
                lineHeight: 1.5,
              }}
            >
              Gates: Annualized and MWR need N ≥ 20 / Beta, Alpha, Tracking Error need N ≥ 40 / VaR and CVaR need N ≥ 60. Insufficient history shows —.
            </div>

            {/* Path and Period supplemental — stacked compact */}
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
                <CardRow label="Current DD" value={fmtPct(summary.current_drawdown)} align="compact" tone={summary.current_drawdown < 0 ? "negative" : "default"} />
                <CardRow label="Drawdown Trough" value={troughDate} align="compact" />
                <CardRow label="Trading Days" value={String(N)} align="compact" />
                <CardRow label="Starting Equity" value={fmtUsdExact(startingEquity)} align="compact" />
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
                  {data.benchmark}
                </span>
              </div>
              <div style={{ padding: "0 8px 8px", display: "flex", flexDirection: "column", gap: 6 }}>
                <CardRow label="Period" value={`${data.period_start} to ${data.period_end}`} align="compact" />
                <CardRow label="Benchmark" value={`${data.benchmark} ${fmtPct(data.benchmark_total_return)}`} align="compact" />
                <CardRow label="As Of" value={data.as_of} align="compact" />
                <CardRow label="Last Sync" value={data.last_sync ? new Date(data.last_sync).toISOString().slice(0, 10) : "—"} align="compact" />
              </div>
            </div>

            {/* Methodology — compact grid */}
            <div
              data-testid="performance-methodology-footer"
              style={{ borderTop: "1px solid var(--border-dim)", padding: 12 }}
            >
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
                    {data.methodology.curve_type}
                  </span>
                </div>
                <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
                  <span className="performance-meta-label" style={{ fontSize: 9 }}>
                    Return Basis
                  </span>
                  <span className="performance-meta-value" data-testid="methodology-return-basis" style={{ fontSize: 11 }}>
                    {data.methodology.return_basis}
                  </span>
                </div>
                <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
                  <span className="performance-meta-label" style={{ fontSize: 9 }}>
                    Library
                  </span>
                  <span className="performance-meta-value" style={{ fontSize: 11 }}>
                    {data.methodology.library_strategy}
                  </span>
                </div>
                <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
                  <span className="performance-meta-label" style={{ fontSize: 9 }}>
                    Risk-Free Rate (T-bill)
                  </span>
                  <span className="performance-meta-value" data-testid="methodology-rf" style={{ fontSize: 11 }}>
                    {rfPct} {(data.methodology.library_strategy.includes("fred") || data.methodology.risk_free_rate > 0) ? "(FRED DGS3MO)" : "(fallback 0)"}
                  </span>
                </div>
                <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
                  <span className="performance-meta-label" style={{ fontSize: 9 }}>
                    Stock History
                  </span>
                  <span className="performance-meta-value" style={{ fontSize: 11 }}>
                    {data.price_sources.stocks}
                  </span>
                </div>
                <div className="performance-meta-item" style={{ padding: "8px 10px" }}>
                  <span className="performance-meta-label" style={{ fontSize: 9 }}>
                    Option History
                  </span>
                  <span className="performance-meta-value" style={{ fontSize: 11 }}>
                    {data.price_sources.options}
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
                TWR Modified Dietz daily, chained geometrically. External flows are excluded from return. First NAV date is the period start. Risk-free is the FRED 3-month T-bill series. No metric is shown under its N gate.
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Metric definition — BottomSheet for thumb-zone ergonomics */}
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
            {activeCard.gated ? (
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
                {activeCard.gateLabel}
              </div>
            ) : null}
          </div>
        ) : null}
      </BottomSheet>
    </div>
  );
}
