"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertTriangle, TrendingDown, TrendingUp } from "lucide-react";
import { MarketState } from "@/lib/useMarketHours";
import {
  useGammaRotation,
  type GammaRotationAsset,
  type GammaRotationData,
  type GammaRotationGate,
  type GammaRotationHistoryEntry,
} from "@/lib/useGammaRotation";
import { isGammaRotationStale } from "@/lib/gammaRotationStaleness";
import InfoTooltip from "./InfoTooltip";
import SpectralLoader from "./SpectralLoader";
import RegimeSyncStatusBadge from "./RegimeSyncStatusBadge";

type GammaRotationPanelProps = {
  marketState?: MarketState;
};

function fmtZ(value: number | null | undefined): string {
  if (value == null) return "---";
  return value >= 0 ? `+${value.toFixed(2)}σ` : `${value.toFixed(2)}σ`;
}

function fmtNum(value: number | null | undefined, decimals = 2): string {
  if (value == null) return "---";
  return value.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function fmtExposure(value: number | null | undefined): string {
  if (value == null) return "---";
  const abs = Math.abs(value);
  const sign = value >= 0 ? "+" : "-";
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${sign}${(abs / 1_000).toFixed(1)}K`;
  return `${sign}${abs.toFixed(0)}`;
}

function fmtPct(value: number | null | undefined): string {
  if (value == null) return "---";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function interpretationLabel(value: string): string {
  switch (value) {
    case "TOP_WATCH": return "TOP WATCH";
    case "BOTTOM_WATCH": return "BOTTOM WATCH";
    case "RISK_ON": return "RISK-ON";
    case "RISK_OFF": return "RISK-OFF";
    case "DUAL_WHIP": return "DUAL WHIP";
    case "CUSHION": return "CUSHION";
    default: return "NORMAL";
  }
}

function interpretationColor(value: string): string {
  switch (value) {
    case "TOP_WATCH": return "var(--warning)";
    case "BOTTOM_WATCH": return "var(--signal-core)";
    case "RISK_ON": return "var(--signal-core)";
    case "RISK_OFF": return "var(--fault)";
    case "DUAL_WHIP": return "var(--fault)";
    case "CUSHION": return "var(--signal-core)";
    default: return "var(--text-muted)";
  }
}

function gateColor(status: string): string {
  switch (status) {
    case "PASS": return "var(--signal-core)";
    case "FAIL": return "var(--fault)";
    default: return "var(--warning)";
  }
}

function assetColor(asset: GammaRotationAsset): string {
  if (asset.state === "CUSHION") return "var(--signal-core)";
  if (asset.state === "WHIP") return "var(--fault)";
  return "var(--text-muted)";
}

function MetricCard({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="grg-metric-card">
      <div className="grg-metric-label">{label}</div>
      <div className="grg-metric-value" style={color ? { color } : undefined}>{value}</div>
      {sub && <div className="grg-metric-sub">{sub}</div>}
    </div>
  );
}

function AssetCard({ asset }: { asset: GammaRotationAsset }) {
  const flip = asset.levels.gex_flip;
  return (
    <div className="grg-asset-card">
      <div className="grg-asset-head">
        <span className="grg-asset-ticker">{asset.ticker}</span>
        <span className="grg-state-pill" style={{ color: assetColor(asset), borderColor: `color-mix(in srgb, ${assetColor(asset)} 42%, var(--line-grid))` }}>
          {asset.state}
        </span>
      </div>
      <div className="grg-asset-main" style={{ color: assetColor(asset) }}>
        {fmtExposure(asset.net_gex)}
      </div>
      <div className="grg-asset-grid">
        <span>gamma z</span><strong>{fmtZ(asset.gamma_z)}</strong>
        <span>spot</span><strong>{fmtNum(asset.spot)}</strong>
        <span>flip</span><strong>{flip ? fmtNum(flip.strike) : "---"}</strong>
        <span>spot vs flip</span><strong>{fmtPct(asset.spot_vs_flip_pct)}</strong>
      </div>
    </div>
  );
}

/**
 * The plot rectangle inside the measured SVG box. Left gutter holds the sigma
 * labels, the bottom gutter holds the date axis.
 */
type ChartBox = { width: number; height: number };
type PlotRect = { left: number; right: number; top: number; bottom: number };

const CHART_PAD = { left: 44, right: 24, top: 20, bottom: 34 };
const FALLBACK_BOX: ChartBox = { width: 708, height: 306 };
/** Below this the axis drops ticks rather than overlapping them. */
const MIN_PX_PER_DATE_TICK = 120;

function plotRect(box: ChartBox): PlotRect {
  return {
    left: CHART_PAD.left,
    right: Math.max(CHART_PAD.left + 1, box.width - CHART_PAD.right),
    top: CHART_PAD.top,
    bottom: Math.max(CHART_PAD.top + 1, box.height - CHART_PAD.bottom),
  };
}

function yFor(value: number, min: number, max: number, rect: PlotRect): number {
  if (max <= min) return (rect.top + rect.bottom) / 2;
  return rect.bottom - ((value - min) / (max - min)) * (rect.bottom - rect.top);
}

function xFor(index: number, count: number, rect: PlotRect): number {
  if (count <= 1) return rect.left;
  return rect.left + (index / (count - 1)) * (rect.right - rect.left);
}

function linePath(history: GammaRotationHistoryEntry[], key: "grg_z" | "spy_gamma_z" | "tlt_gamma_z", min: number, max: number, rect: PlotRect): string {
  let segmentOpen = false;
  const commands: string[] = [];
  history.forEach((row, index) => {
    const value = row[key];
    if (value == null || !Number.isFinite(value)) {
      segmentOpen = false;
      return;
    }
    commands.push(`${segmentOpen ? "L" : "M"}${xFor(index, history.length, rect).toFixed(1)} ${yFor(value, min, max, rect).toFixed(1)}`);
    segmentOpen = true;
  });
  return commands.join(" ");
}

/**
 * Evenly spaced session indices for the date axis, always including the first
 * and last so the axis states the range it covers. Count is driven by the
 * measured width so labels never collide.
 */
function dateTickIndices(count: number, plotWidth: number): number[] {
  if (count <= 0) return [];
  if (count === 1) return [0];
  const fit = Math.floor(plotWidth / MIN_PX_PER_DATE_TICK);
  const ticks = Math.max(2, Math.min(7, fit, count));
  const step = (count - 1) / (ticks - 1);
  const indices = Array.from({ length: ticks }, (_, i) => Math.round(i * step));
  return [...new Set(indices)];
}

/** Measures the chart host so the SVG can draw in pixel space and fill it. */
function useChartBox(ref: React.RefObject<HTMLDivElement | null>): ChartBox {
  const [box, setBox] = useState<ChartBox>(FALLBACK_BOX);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect || rect.width <= 0 || rect.height <= 0) return;
      setBox({ width: rect.width, height: rect.height });
    });
    observer.observe(el);
    const initial = el.getBoundingClientRect();
    if (initial.width > 0 && initial.height > 0) {
      setBox({ width: initial.width, height: initial.height });
    }
    return () => observer.disconnect();
  }, [ref]);

  return box;
}

function domainLabel(value: number): string {
  return `${value > 0 ? "+" : ""}${Number.isInteger(value) ? value : value.toFixed(1)}σ`;
}

function GammaRotationChart({ history }: { history: GammaRotationHistoryEntry[] }) {
  const plotRef = useRef<HTMLDivElement>(null);
  const box = useChartBox(plotRef);
  const rect = plotRect(box);

  const values = history.flatMap((row) => [row.grg_z, row.spy_gamma_z, row.tlt_gamma_z]).filter((value): value is number => value != null && Number.isFinite(value));
  const min = Math.min(-3, ...values);
  const max = Math.max(3, ...values);
  const zeroY = yFor(0, min, max, rect);

  const gridRows = [0, 0.25, 0.5, 0.75, 1].map((t) => rect.top + t * (rect.bottom - rect.top));
  const ticks = useMemo(
    () => dateTickIndices(history.length, rect.right - rect.left),
    [history.length, rect.left, rect.right],
  );

  return (
    <div className="grg-chart-wrap" data-testid="grg-chart">
      <div className="grg-chart-head">
        <span>90-session divergence field</span>
        <span className="grg-chart-legend">
          <i style={{ background: "var(--warning)" }} /> GRG
          <i style={{ background: "var(--signal-core)" }} /> SPY
          <i style={{ background: "var(--fault)" }} /> TLT
        </span>
      </div>
      <div className="grg-chart-plot" ref={plotRef}>
        <svg
          width="100%"
          height="100%"
          viewBox={`0 0 ${Math.round(box.width)} ${Math.round(box.height)}`}
          role="img"
          aria-label="Gamma Rotation Gap history"
        >
          <g stroke="var(--chart-grid)" strokeWidth="1">
            {gridRows.map((y) => <line key={y} x1={rect.left} y1={y} x2={rect.right} y2={y} />)}
            {ticks.map((index) => {
              const x = xFor(index, history.length, rect);
              return <line key={`v${index}`} x1={x} y1={rect.top} x2={x} y2={rect.bottom} />;
            })}
          </g>
          <line x1={rect.left} y1={zeroY} x2={rect.right} y2={zeroY} stroke="var(--text-muted)" strokeDasharray="4 6" opacity="0.65" />
          <path d={linePath(history, "spy_gamma_z", min, max, rect)} fill="none" stroke="var(--signal-core)" strokeWidth="2" opacity="0.8" />
          <path d={linePath(history, "tlt_gamma_z", min, max, rect)} fill="none" stroke="var(--fault)" strokeWidth="2" opacity="0.8" />
          <path d={linePath(history, "grg_z", min, max, rect)} fill="none" stroke="var(--warning)" strokeWidth="3" />
          <text x="8" y={rect.top + 8} className="grg-svg-label">{domainLabel(max)}</text>
          <text x="20" y={zeroY - 4} className="grg-svg-label">0</text>
          <text x="8" y={rect.bottom} className="grg-svg-label">{domainLabel(min)}</text>
          {ticks.map((index, position) => {
            const row = history[index];
            if (!row) return null;
            const anchor = position === 0 ? "start" : position === ticks.length - 1 ? "end" : "middle";
            return (
              <text
                key={`t${index}`}
                data-testid="grg-x-tick"
                x={xFor(index, history.length, rect)}
                y={rect.bottom + 20}
                textAnchor={anchor}
                className="grg-svg-label"
              >
                {row.date}
              </text>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function GateList({ gates }: { gates: GammaRotationGate[] }) {
  return (
    <div className="grg-gates">
      {gates.map((gate) => (
        <div className="grg-gate" key={gate.id}>
          <div className="grg-gate-label">{gate.label}</div>
          <div className="grg-gate-copy">{gate.copy}</div>
          <div className="grg-gate-status" style={{ color: gateColor(gate.status) }}>{gate.status}</div>
        </div>
      ))}
    </div>
  );
}

function GammaRotationBody({ data, lastSync, syncing }: { data: GammaRotationData; lastSync: string | null; syncing: boolean }) {
  const tone = interpretationColor(data.signal.interpretation);
  const gammaRotationIsStale = isGammaRotationStale(data);
  const freshnessState = syncing ? "syncing" : gammaRotationIsStale ? "stale" : data.market_open ? "live" : "fresh";
  const freshnessBadgeClass = `grg-status-badge grg-status-badge-${freshnessState}`;
  return (
    <div className="section grg-panel regime-relationship-panel">
      <div className="regime-relationship-panel-head">
        <div className="regime-panel-title">
          <Activity size={14} />
          Gamma Rotation Gap
          <InfoTooltip
            text="Gamma Rotation Gap compares normalized SPY dealer gamma against normalized TLT dealer gamma. Positive extremes mean equity cushion with duration fragility. Negative extremes mean equity stress with duration cushion."
            triggerTestId="grg-section-tooltip-trigger"
            contentTestId="grg-section-tooltip-content"
          />
        </div>
        <div className="grg-header-meta">
          <RegimeSyncStatusBadge
            state={freshnessState}
            className={freshnessBadgeClass}
            testId="grg-freshness-badge"
          />
          <span className="grg-badge" style={{ color: tone, borderColor: `color-mix(in srgb, ${tone} 42%, var(--line-grid))` }}>
            {interpretationLabel(data.signal.interpretation)}
          </span>
          <span>{data.data_date || "no date"}</span>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString()}
            </span>
          )}
        </div>
      </div>

      <div className="grg-body">
        <div className="grg-hero">
          <div>
            <div className="grg-eyebrow">GRG residual</div>
            <div className="grg-score" style={{ color: tone }}>{fmtZ(data.signal.grg_z)}</div>
            <div className="grg-state">{data.signal.state_label}</div>
            <p className="grg-summary">{data.signal.summary}</p>
          </div>
          <div className="grg-metric-grid">
            <MetricCard label="SPY GEX" value={fmtExposure(data.assets.SPY.net_gex)} sub={`${data.assets.SPY.state} / ${fmtZ(data.assets.SPY.gamma_z)}`} color={assetColor(data.assets.SPY)} />
            <MetricCard label="TLT GEX" value={fmtExposure(data.assets.TLT.net_gex)} sub={`${data.assets.TLT.state} / ${fmtZ(data.assets.TLT.gamma_z)}`} color={assetColor(data.assets.TLT)} />
            <MetricCard label="Top gate" value={`${data.signal.top_score}/5`} sub={data.top_bottom.top.active ? "active" : "inactive"} color={data.signal.top_score >= 4 ? "var(--warning)" : "var(--text-primary)"} />
            <MetricCard label="Bottom gate" value={`${data.signal.bottom_score}/5`} sub={data.top_bottom.bottom.active ? "active" : "inactive"} color={data.signal.bottom_score >= 4 ? "var(--signal-core)" : "var(--text-primary)"} />
          </div>
        </div>

        <div className="grg-layout">
          <div className="grg-stack">
            <AssetCard asset={data.assets.SPY} />
            <AssetCard asset={data.assets.TLT} />
          </div>
          <GammaRotationChart history={data.history} />
        </div>

        <div className="grg-bottom">
          <div className="grg-card">
            <div className="grg-card-title">
              <TrendingDown size={14} /> Top identification
            </div>
            <p>{data.top_bottom.top.copy}</p>
            <strong style={{ color: data.top_bottom.top.active ? "var(--warning)" : "var(--text-muted)" }}>
              {data.top_bottom.top.active ? "Watch active" : "No confirmed top watch"}
            </strong>
          </div>
          <div className="grg-card">
            <div className="grg-card-title">
              <TrendingUp size={14} /> Bottom identification
            </div>
            <p>{data.top_bottom.bottom.copy}</p>
            <strong style={{ color: data.top_bottom.bottom.active ? "var(--signal-core)" : "var(--text-muted)" }}>
              {data.top_bottom.bottom.active ? "Watch active" : "No confirmed bottom watch"}
            </strong>
          </div>
          <div className="grg-card">
            <div className="grg-card-title">
              <AlertTriangle size={14} /> Signal gates
            </div>
            <GateList gates={data.gates} />
          </div>
        </div>
      </div>
    </div>
  );
}

export default function GammaRotationPanel({ marketState }: GammaRotationPanelProps) {
  const { data, loading, error, lastSync, syncing } = useGammaRotation(marketState ?? null);

  if (loading && !data) {
    return (
      <div className="section">
        <div className="section-header">
          <div className="section-title"><Activity size={14} /> Gamma Rotation Gap</div>
        </div>
        <div className="section-body">
          <SpectralLoader label="Sampling SPY and TLT gamma rotation" />
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="section">
        <div className="section-header">
          <div className="section-title"><Activity size={14} /> Gamma Rotation Gap</div>
        </div>
        <div className="section-body" style={{ padding: "16px" }}>
          <div className="alert-item bearish">{error}</div>
        </div>
      </div>
    );
  }

  if (!data || !data.history.length) {
    return (
      <div className="section">
        <div className="section-header">
          <div className="section-title"><Activity size={14} /> Gamma Rotation Gap</div>
        </div>
        <div className="section-body" style={{ padding: "24px", textAlign: "center" }}>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)" }}>
            No Gamma Rotation Gap data available. Run a scan to populate SPY/TLT gamma rotation.
          </span>
        </div>
      </div>
    );
  }

  return <GammaRotationBody data={data} lastSync={lastSync} syncing={syncing} />;
}
