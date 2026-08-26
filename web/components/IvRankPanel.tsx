"use client";

import { useEffect, useMemo, useState } from "react";
import { Gauge } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import PanelRefreshError from "./PanelRefreshError";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import FreshnessRail from "./FreshnessRail";
import { IV_RANK_REFRESH } from "@/lib/refreshSchedule";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import {
  defaultPresetForLength,
  presetRange,
  presetSessions,
  type RangePresetSlug,
} from "@/lib/historyRange";
import {
  RANK_WINDOW,
  buildIvRankChartRows,
  formatIvPercent,
  formatRank,
  ivrankRegime,
  ivrankRegimeColor,
  type IvRankChartRow,
} from "@/lib/ivrank";
import { useIvRank } from "@/lib/useIvRank";
import { useViewport } from "@/lib/useViewport";

/**
 * IV RANK regime tab. Descriptive read of how rich or cheap SPY 1M implied
 * volatility is against its trailing year, and nothing more. No validation
 * study was run, so no copy in this file may claim forward information.
 *
 * Spec: docs/indicators/ivrank.md sections B.4, F.3, G.
 */

const IVRANK_TOOLTIP =
  "The rank of SPY's 30-day implied volatility within its trailing 252-session range. 0 is the cheapest 1M vol of the year, 100 the richest. A low rank means option premium is cheap relative to the past year, not that volatility is about to move. Below 20 is SUPPRESSED, 20 to 50 NORMAL, 50 to 80 ELEVATED, 80 and above EXTREME. A companion percentile reports the share of trailing sessions with IV strictly below the latest close.";

const SOURCE_FOOTNOTE =
  `Source: Interactive Brokers SPY 30-day implied volatility closes, Unusual Whales fallback. Rank and percentile span the trailing ${RANK_WINDOW} sessions, inclusive of the session. This is a regime description of option premium, nothing more.`;

const EMPTY_SECONDARY =
  "The ivrank refresh timer populates this tab from the SPY 30-day implied volatility history. Data appears after the first successful pull.";

/** 0-100 payload percent to one decimal: 11.952191 -> "12.0%"; "---" unavailable. */
function formatPctPoints(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v.toFixed(1)}%`;
}

/** "10.5% - 26.3%" from the current window's low/high; "---" when either is out. */
function formatIvRange(low: number | null | undefined, high: number | null | undefined): string {
  if (low == null || !Number.isFinite(low) || high == null || !Number.isFinite(high)) return "---";
  return `${formatIvPercent(low)} - ${formatIvPercent(high)}`;
}

/** Daily-series x-tick over a multi-year span: "05 Aug 26". */
function formatDayTick(d: Date): string {
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "2-digit",
    timeZone: "UTC",
  });
}

export default function IvRankPanel() {
  const { data, loading, syncing, error } = useIvRank();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [preset, setPreset] = useState<RangePresetSlug | "custom" | null>(null);
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const series = data?.series ?? [];
  const total = series.length;
  const activePreset: RangePresetSlug | "custom" = preset ?? defaultPresetForLength(total);

  useEffect(() => {
    if (preset == null || preset === "custom" || preset === "all") return;
    if (presetSessions(preset) > total) setPreset("all");
  }, [preset, total]);

  const chartRange = useMemo<[number, number]>(() => {
    if (total < 2) return [0, Math.max(total - 1, 0)];
    if (activePreset === "custom" && customRange) {
      const max = total - 1;
      const end = Math.min(customRange[1], max);
      const start = Math.max(0, Math.min(customRange[0], end));
      return [start, end];
    }
    return presetRange(activePreset === "custom" ? "all" : activePreset, total);
  }, [activePreset, customRange, total]);

  if ((loading || syncing) && !data) {
    return <SpectralLoader label="Loading SPY IV rank series" />;
  }

  if (!data || data.missing || !data.current || series.length === 0) {
    return (
      <SectionEmptyState
        icon={Gauge}
        headline="No SPY IV rank data yet"
        secondary={EMPTY_SECONDARY}
      />
    );
  }

  const current = data.current;
  const regime = ivrankRegime(current.iv_rank) ?? current.regime;
  const regimeTone = ivrankRegimeColor(regime);
  const uwCheck = data.uw_check;
  const uwSub =
    uwCheck && uwCheck.iv_rank != null ? `UW CROSS-CHECK ${formatRank(uwCheck.iv_rank)}` : null;

  const rows = buildIvRankChartRows(series);
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<IvRankChartRow>, ChartSeries<IvRankChartRow>] = [
    {
      key: "iv",
      label: "1M IV",
      color: chartSeriesColor("comparison"),
      axis: "left",
      format: (v: number) => `${(v * 100).toFixed(1)}%`,
    },
    {
      key: "iv_rank",
      label: "RANK",
      color: chartSeriesColor("primary"),
      axis: "right",
      format: (v: number) => v.toFixed(0),
    },
  ];

  const clock = data.scan_time
    ? new Date(data.scan_time).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })
    : null;

  return (
    <>
      {/* ── Summary strip ─────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Gauge size={14} />
            SPY 1M IV Rank
            <InfoTooltip text={IVRANK_TOOLTIP} />
          </div>
          <PanelRefreshError error={error} testId="ivrank-refresh-error" />
          {clock && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {clock}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2 m-regime-grid2x2--fill-last" data-testid="ivrank-mobile-grid">
            <MetricCell label="IV RANK" value={formatRank(current.iv_rank)} />
            <MetricCell label="REGIME" value={regime ?? "---"} />
            <MetricCell label="1M IV" value={formatIvPercent(current.iv)} />
            <MetricCell label="IV PCTILE" value={formatPctPoints(current.iv_pct)} />
            <MetricCell label="1Y IV RANGE" value={formatIvRange(current.iv_1y_low, current.iv_1y_high)} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="ivrank-strip-rank"
              label="IV RANK"
              value={
                <span data-testid="ivrank-rank-value" style={{ color: regimeTone }}>
                  {formatRank(current.iv_rank)}
                </span>
              }
              sub={
                uwSub ? (
                  <span data-testid="ivrank-uw-check" style={{ color: "var(--text-muted)" }}>
                    {uwSub}
                  </span>
                ) : (
                  <>1D {formatRank(current.rank_change_1d)}</>
                )
              }
            />
            <RegimeStripCell
              testId="ivrank-strip-iv"
              label="1M IV"
              value={formatIvPercent(current.iv)}
              sub={<>30D ATM IMPLIED VOL</>}
            />
            <RegimeStripCell
              testId="ivrank-strip-pctile"
              label="IV PCTILE"
              value={formatPctPoints(current.iv_pct)}
              sub={<>SHARE OF SESSIONS BELOW</>}
            />
            <RegimeStripCell
              testId="ivrank-strip-range"
              label="1Y IV RANGE"
              value={formatIvRange(current.iv_1y_low, current.iv_1y_high)}
              sub={<>{RANK_WINDOW} SESSION LOW - HIGH</>}
            />
            <RegimeStripCell
              testId="ivrank-strip-regime"
              label="REGIME"
              value={
                <span
                  data-testid="ivrank-regime-value"
                  style={{ color: regimeTone, whiteSpace: "nowrap" }}
                >
                  {regime ?? "---"}
                </span>
              }
              sub={<>PREMIUM VS TRAILING YEAR</>}
            />
          </RegimeStrip>
        )}

        <FreshnessRail
          schedule={IV_RANK_REFRESH}
          asOf={data.as_of ?? current.date}
          testId="ivrank-freshness-rail"
          asOfTestId="ivrank-strip-asof"
        />
      </div>

      {/* ── Rank over the 1M IV series ────────────────────── */}
      <div className="breadth-history-block" data-testid="ivrank-chart-section">
        <HistoryRangeChips
          active={activePreset}
          onChange={(slug) => {
            setCustomRange(null);
            setPreset(slug);
          }}
          maxSessions={total}
          ariaLabel="SPY IV rank chart range"
          dataTestId="ivrank-range-chips"
        />

        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="SPY 1M IV RANK"
          xTickFormat={formatDayTick}
        />

        {total >= 2 && (
          <BrushMinimap
            values={series.map((entry) => entry.iv_rank)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="ivrank-brush"
            ariaLabel="SPY IV rank history range brush"
          />
        )}

        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "9px",
            color: "var(--text-muted)",
            marginTop: "8px",
          }}
        >
          {SOURCE_FOOTNOTE}
        </div>
      </div>
    </>
  );
}
