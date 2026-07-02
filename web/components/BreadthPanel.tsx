"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import ChartPanel from "./charts/ChartPanel";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { PointChange, RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import {
  defaultPresetForLength,
  presetRange,
  presetSessions,
  type RangePresetSlug,
} from "@/lib/historyRange";
import {
  useBreadth,
  type BreadthDivergence,
  type BreadthHistoryEntry,
  type BreadthIntradayPoint,
} from "@/lib/useBreadth";
import { MarketState } from "@/lib/useMarketHours";
import { useViewport } from "@/lib/useViewport";

type BreadthPanelProps = {
  marketState?: MarketState;
};

/* ─── Helpers ─────────────────────────────────────────── */

function fmtNum(v: number | null | undefined, decimals = 0): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(decimals);
}

function fmtSigned(v: number | null | undefined, decimals = 0): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v >= 0 ? "+" : ""}${v.toFixed(decimals)}`;
}

/**
 * Divergence rule (user-facing semantics — must match the backend collector):
 * over the last 20 sessions, spy_change_20d_pct > 1 AND cum_ad_change_20d < 0
 * -> "bearish"; spy_change_20d_pct < -1 AND cum_ad_change_20d > 0 ->
 * "bullish"; else "none"; null when inputs missing.
 */
function divergenceLabel(divergence: BreadthDivergence): string {
  if (divergence == null) return "---";
  return divergence.toUpperCase();
}

function divergenceColor(divergence: BreadthDivergence): string {
  switch (divergence) {
    case "bearish": return "var(--negative)";
    case "bullish": return "var(--positive)";
    default:        return "var(--text-muted)";
  }
}

function signTone(v: number | null | undefined): "pos" | "neg" | "mut" {
  if (v == null || !Number.isFinite(v) || v === 0) return "mut";
  return v > 0 ? "pos" : "neg";
}

function signColor(v: number | null | undefined): string {
  const tone = signTone(v);
  if (tone === "pos") return "var(--positive)";
  if (tone === "neg") return "var(--negative)";
  return "var(--text-muted)";
}

function fmtSessionTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
  });
}

/* ─── Session Net A/D intraday chart ──────────────────
   Declarative JSX-SVG (RegimeRelationshipView's approach) over the
   latest session's 5-min net_ad bars. The zero baseline is emphasized;
   the trace is clipped at zero so values >= 0 render in var(--positive)
   and values < 0 in var(--negative). Fixed viewBox stretched via
   preserveAspectRatio="none"; axis labels live in HTML so text never
   distorts. */

const INTRADAY_VBW = 1000;
const INTRADAY_VBH = 220;

type IntradayHover = {
  point: BreadthIntradayPoint;
  x: number;
  y: number;
  width: number;
};

function SessionNetAdChart({ intraday }: { intraday: BreadthIntradayPoint[] }) {
  const [hover, setHover] = useState<IntradayHover | null>(null);

  const geometry = useMemo(() => {
    if (intraday.length < 2) return null;
    const values = intraday.map((p) => p.net_ad);
    const min = Math.min(...values, 0);
    const max = Math.max(...values, 0);
    const pad = (max - min) * 0.12 || 1;
    const lo = min - pad;
    const hi = max + pad;
    const y = (v: number) => ((hi - v) / (hi - lo)) * INTRADAY_VBH;
    const x = (i: number) => (i / (intraday.length - 1)) * INTRADAY_VBW;
    const zeroY = y(0);
    const linePath = intraday
      .map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(p.net_ad).toFixed(2)}`)
      .join(" ");
    const areaPath = `M0,${zeroY.toFixed(2)} ${intraday
      .map((p, i) => `L${x(i).toFixed(2)},${y(p.net_ad).toFixed(2)}`)
      .join(" ")} L${INTRADAY_VBW},${zeroY.toFixed(2)} Z`;
    return { zeroY, linePath, areaPath };
  }, [intraday]);

  if (!geometry) {
    return (
      <div className="chart-empty-state breadth-intraday-empty" data-testid="breadth-intraday-empty">
        NO SESSION DATA
      </div>
    );
  }

  const { zeroY, linePath, areaPath } = geometry;

  function handleMove(event: React.MouseEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0) return;
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const idx = Math.round(ratio * (intraday.length - 1));
    setHover({
      point: intraday[idx],
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
      width: rect.width,
    });
  }

  return (
    <div
      className="breadth-intraday-shell"
      onMouseMove={handleMove}
      onMouseLeave={() => setHover(null)}
    >
      <svg
        className="breadth-intraday-svg"
        viewBox={`0 0 ${INTRADAY_VBW} ${INTRADAY_VBH}`}
        preserveAspectRatio="none"
        aria-label="Session net advance/decline"
        data-testid="breadth-intraday-svg"
      >
        <defs>
          <clipPath id="breadth-clip-above">
            <rect x={0} y={0} width={INTRADAY_VBW} height={zeroY} />
          </clipPath>
          <clipPath id="breadth-clip-below">
            <rect x={0} y={zeroY} width={INTRADAY_VBW} height={INTRADAY_VBH - zeroY} />
          </clipPath>
        </defs>
        <path
          d={areaPath}
          fill="color-mix(in srgb, var(--positive) 14%, transparent)"
          clipPath="url(#breadth-clip-above)"
        />
        <path
          d={areaPath}
          fill="color-mix(in srgb, var(--negative) 14%, transparent)"
          clipPath="url(#breadth-clip-below)"
        />
        <path
          d={linePath}
          fill="none"
          stroke="var(--positive)"
          strokeWidth={2}
          vectorEffect="non-scaling-stroke"
          clipPath="url(#breadth-clip-above)"
        />
        <path
          d={linePath}
          fill="none"
          stroke="var(--negative)"
          strokeWidth={2}
          vectorEffect="non-scaling-stroke"
          clipPath="url(#breadth-clip-below)"
        />
        {/* Emphasized zero baseline — the advance/decline balance point. */}
        <line
          x1={0}
          x2={INTRADAY_VBW}
          y1={zeroY}
          y2={zeroY}
          stroke="var(--chart-axis, var(--border-dim))"
          strokeWidth={2}
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="breadth-intraday-axis">
        <span>{fmtSessionTime(intraday[0].time)}</span>
        <span>{fmtSessionTime(intraday[Math.floor((intraday.length - 1) / 2)].time)}</span>
        <span>{fmtSessionTime(intraday[intraday.length - 1].time)}</span>
      </div>
      {hover && (
        <div
          className="chart-tooltip"
          style={{
            top: hover.y - 10,
            ...(hover.x > hover.width / 2 ? { right: 12 } : { left: hover.x + 12 }),
          }}
        >
          <div className="chart-tooltip-date">{fmtSessionTime(hover.point.time)}</div>
          <div className="chart-tooltip-row">
            <span className="chart-tooltip-label">NET A/D</span>
            <span
              className="chart-tooltip-value"
              style={{ color: signColor(hover.point.net_ad) }}
            >
              {fmtSigned(hover.point.net_ad)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Main component ─────────────────────────────────── */

export default function BreadthPanel({ marketState }: BreadthPanelProps) {
  const { data, loading, syncing, lastSync } = useBreadth(marketState ?? null);
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  // Chart range: preset chips (1M / 3M / 6M / 1Y / All) OR a custom window
  // from the brush minimap — mirroring the VCG history chart wiring.
  const historyLength = data?.history?.length ?? 0;
  const [activeRange, setActiveRange] = useState<RangePresetSlug | "custom">(() =>
    historyLength >= 21 ? "1m" : defaultPresetForLength(historyLength),
  );
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);
  const [rangeTouched, setRangeTouched] = useState(false);

  // Re-pick the default preset when history grows past a depth threshold
  // (e.g. backfill extends 3M → 1Y) — only while the user hasn't touched it.
  useEffect(() => {
    if (rangeTouched || activeRange === "custom") return;
    const ideal = historyLength >= 21 ? "1m" : defaultPresetForLength(historyLength);
    if (activeRange === "all" && historyLength < 21) return;
    if (presetSessions(activeRange) > historyLength) {
      setActiveRange(ideal);
    } else if (activeRange === "all" && historyLength >= 21) {
      setActiveRange("1m");
    }
  }, [historyLength, activeRange, rangeTouched]);

  const chartRange = useMemo<[number, number]>(() => {
    if (historyLength < 2) return [0, Math.max(historyLength - 1, 0)];
    if (activeRange === "custom" && customRange) {
      const max = historyLength - 1;
      const end = Math.min(customRange[1], max);
      const start = Math.max(0, Math.min(customRange[0], end));
      return [start, end];
    }
    const slug: RangePresetSlug = activeRange === "custom" ? "1m" : activeRange;
    return presetRange(slug, historyLength);
  }, [activeRange, customRange, historyLength]);

  if ((loading || syncing) && !data) {
    return <SpectralLoader label="Sampling NYSE advance/decline breadth" />;
  }

  if (!data || data.missing || !data.latest) {
    return (
      <SectionEmptyState
        icon={Activity}
        headline="No breadth data yet"
        secondary="Breadth samples automatically while this tab is open. Data appears once the IB gateway serves NYSE internals."
      />
    );
  }

  const latest = data.latest;
  const netAdDelta =
    latest.net_ad != null && latest.net_ad_prev != null
      ? latest.net_ad - latest.net_ad_prev
      : null;
  const divergenceChip = (
    <span
      className="breadth-divergence-chip"
      data-testid="breadth-divergence-chip"
      style={{
        color: divergenceColor(latest.divergence),
        background: `color-mix(in srgb, ${divergenceColor(latest.divergence)} 12%, transparent)`,
      }}
    >
      {divergenceLabel(latest.divergence)}
    </span>
  );

  const breadthSeries: [ChartSeries<BreadthHistoryEntry>, ChartSeries<BreadthHistoryEntry>] = [
    { key: "cum_ad", label: "CUM A/D", color: chartSeriesColor("primary"), axis: "left", format: (v: number) => v.toFixed(0) },
    { key: "spy_close", label: "SPY", color: chartSeriesColor("comparison"), axis: "right", format: (v: number) => `$${v.toFixed(2)}` },
  ];

  return (
    <>
      {/* ── Summary strip ─────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Activity size={14} />
            NYSE Breadth
            <InfoTooltip text="Market breadth from NYSE internals: net advance/decline (AD-NYSE), TICK-NYSE, and the cumulative A/D line vs SPY. A 20-session divergence between the A/D line and SPY flags participation thinning under a rising tape (bearish) or improving under a falling tape (bullish)." />
          </div>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <>
            <div className="m-breadth-divergence-row">
              <span>DIVERGENCE</span>
              {divergenceChip}
            </div>
            <div className="m-regime-grid2x2" data-testid="breadth-mobile-grid">
              <MetricCell label="NET A/D" value={fmtSigned(latest.net_ad)} tone={signTone(latest.net_ad)} />
              <MetricCell label="TICK" value={fmtSigned(latest.tick)} tone={signTone(latest.tick)} />
              <MetricCell label="CUM A/D 20D" value={fmtSigned(latest.cum_ad_change_20d)} tone={signTone(latest.cum_ad_change_20d)} />
              <MetricCell
                label="SESSIONS POS"
                value={latest.sessions_positive_20 != null ? `${latest.sessions_positive_20}/20` : "---"}
              />
            </div>
          </>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="breadth-strip-net-ad"
              label="NET A/D"
              value={<span style={{ color: signColor(latest.net_ad) }}>{fmtSigned(latest.net_ad)}</span>}
              change={<PointChange change={netAdDelta} label="vs prior" />}
              sub={<>ADVANCERS MINUS DECLINERS</>}
            />
            <RegimeStripCell
              testId="breadth-strip-tick"
              label="TICK"
              value={fmtSigned(latest.tick)}
              sub={<>TICK-NYSE LAST</>}
            />
            <RegimeStripCell
              testId="breadth-strip-cum-ad-20d"
              label="CUM A/D 20D"
              value={
                <span style={{ color: signColor(latest.cum_ad_change_20d) }}>
                  {fmtSigned(latest.cum_ad_change_20d)}
                </span>
              }
              sub={<>CUM A/D {fmtNum(latest.cum_ad)}</>}
            />
            <RegimeStripCell
              testId="breadth-strip-sessions-positive"
              label="SESSIONS POSITIVE"
              value={latest.sessions_positive_20 != null ? `${latest.sessions_positive_20}/20` : "---"}
              sub={<>SPY 20D {fmtSigned(latest.spy_change_20d_pct, 2)}%</>}
            />
            <RegimeStripCell
              testId="breadth-strip-divergence"
              label="DIVERGENCE"
              value={divergenceChip}
              sub={<>A/D LINE VS SPY, 20 SESSIONS</>}
            />
          </RegimeStrip>
        )}
      </div>

      {/* ── Cumulative A/D line vs SPY ─────────────────────── */}
      {data.history.length >= 2 && (() => {
        const [start, end] = chartRange;
        const slice = data.history.slice(start, end + 1);
        return (
          <div className="breadth-history-block" data-testid="breadth-history-chart-section">
            <HistoryRangeChips
              active={activeRange}
              onChange={(preset) => {
                setRangeTouched(true);
                setCustomRange(null);
                setActiveRange(preset);
              }}
              maxSessions={data.history.length}
              ariaLabel="Breadth chart range"
              dataTestId="breadth-history-range-chips"
            />
            <CriHistoryChart
              history={slice}
              series={breadthSeries}
              title="CUMULATIVE A/D LINE VS SPY"
            />
            <BrushMinimap
              values={data.history.map((h) => h.cum_ad ?? 0)}
              range={chartRange}
              onRangeChange={(r) => setCustomRange(r)}
              onCustom={() => {
                setRangeTouched(true);
                setActiveRange("custom");
              }}
              testIdPrefix="breadth-history-brush"
              ariaLabel="Breadth history range brush"
            />
          </div>
        );
      })()}

      {/* ── Session net A/D (latest session, 5-min bars) ──── */}
      <ChartPanel
        family="live-trace"
        title="SESSION NET A/D"
        legend={[
          { label: "ADVANCING", color: "var(--positive)" },
          { label: "DECLINING", color: "var(--negative)" },
        ]}
        className="chart-panel-inline"
        dataTestId="breadth-intraday-chart"
      >
        <SessionNetAdChart intraday={data.intraday} />
      </ChartPanel>
    </>
  );
}
