"use client";

import { useMemo, useState } from "react";
import { Activity } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import {
  defaultPresetForLength,
  presetRange,
  type RangePresetSlug,
} from "@/lib/historyRange";
import {
  buildStraddleChartRows,
  formatHitRate,
  formatRatio,
  formatSourceDate,
  formatStraddlePct,
  ratioColor,
  type StraddleChartRow,
} from "@/lib/straddle";
import { useStraddle } from "@/lib/useStraddle";
import { useViewport } from "@/lib/useViewport";

const STRADDLE_TOOLTIP =
  "Signed SPX daily move divided by the prior close's implied 1-day straddle (0.8 x VIX1D x sqrt(1/252)). Beyond +1 or -1 the move beat the straddle breakeven: buyers of 1-day vol won. Persistent readings beyond 1 mean 0DTE vol is underpriced; readings pinned inside the band mean it is rich.";

const SOURCE_FOOTNOTE =
  "Cboe SPX and VIX1D daily closes since 2022-05. Stats span all sessions with a computable ratio, not the visible range.";

/** Unsigned two-decimal display for the distribution stats. */
function formatStat(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}

function ratioTone(v: number | null | undefined): "pos" | "neg" | "mut" {
  const color = ratioColor(v);
  if (color === "var(--positive)") return "pos";
  if (color === "var(--negative)") return "neg";
  return "mut";
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

export default function StraddlePanel() {
  const { data, loading, syncing, lastSync } = useStraddle();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [preset, setPreset] = useState<RangePresetSlug | "custom" | null>(null);
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const series = data?.series ?? [];
  const total = series.length;
  const activePreset: RangePresetSlug | "custom" = preset ?? defaultPresetForLength(total);

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
    return <SpectralLoader label="Loading Cboe straddle series" />;
  }

  if (!data || data.missing || !data.current || !data.stats || series.length === 0) {
    return (
      <SectionEmptyState
        icon={Activity}
        headline="No straddle data yet"
        secondary="The straddle refresh timer populates this tab from Cboe's SPX and VIX1D daily series. Data appears after the first successful pull."
      />
    );
  }

  const current = data.current;
  const stats = data.stats;
  const vix1dLastModified = data.source_last_modified?.vix1d ?? null;

  const rows = buildStraddleChartRows(series);
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<StraddleChartRow>, ChartSeries<StraddleChartRow>] = [
    {
      key: "spx",
      label: "SPX",
      color: chartSeriesColor("primary"),
      axis: "left",
      scaleType: "linear",
      format: (v: number) => v.toFixed(0),
    },
    {
      key: "ratio",
      label: "REALIZED / IMPLIED 1D STRADDLE",
      color: chartSeriesColor("comparison"),
      axis: "right",
      scaleType: "linear",
      format: formatRatio,
    },
  ];

  return (
    <>
      {/* ── Summary strip ─────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Activity size={14} />
            1-Day Straddle Ratio
            <InfoTooltip text={STRADDLE_TOOLTIP} />
          </div>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="straddle-mobile-grid">
            <MetricCell label="LAST" value={formatRatio(current.ratio)} tone={ratioTone(current.ratio)} />
            <MetricCell label="AVG" value={formatStat(stats.avg)} />
            <MetricCell label="STDDEV" value={formatStat(stats.stddev)} />
            <MetricCell label="HIGH" value={formatStat(stats.high)} />
            <MetricCell label="LOW" value={formatStat(stats.low)} />
            <MetricCell label="HIT RATE" value={formatHitRate(stats.hit_rate)} />
            <MetricCell label="IMPLIED 1D" value={formatStraddlePct(current.implied_straddle_pct)} />
            <MetricCell label="DATE" value={current.date} />
            <MetricCell label="SOURCE" value={formatSourceDate(vix1dLastModified)} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="straddle-strip-last"
              label="LAST"
              value={
                <span data-testid="straddle-last-value" style={{ color: ratioColor(current.ratio) }}>
                  {formatRatio(current.ratio)}
                </span>
              }
              sub={<>BEYOND +/-1 BEAT BREAKEVEN</>}
            />
            <RegimeStripCell
              testId="straddle-strip-avg"
              label="AVG"
              value={formatStat(stats.avg)}
              sub={<>ALL NON NULL RATIOS</>}
            />
            <RegimeStripCell
              testId="straddle-strip-stddev"
              label="STDDEV"
              value={formatStat(stats.stddev)}
              sub={<>POPULATION</>}
            />
            <RegimeStripCell
              testId="straddle-strip-high"
              label="HIGH"
              value={formatStat(stats.high)}
              sub={<>LARGEST UPSIDE OVERSHOOT</>}
            />
            <RegimeStripCell
              testId="straddle-strip-low"
              label="LOW"
              value={formatStat(stats.low)}
              sub={<>LARGEST DOWNSIDE OVERSHOOT</>}
            />
            <RegimeStripCell
              testId="straddle-strip-hit-rate"
              label="HIT RATE"
              value={formatHitRate(stats.hit_rate)}
              sub={<>SESSIONS BEYOND BREAKEVEN</>}
            />
            <RegimeStripCell
              testId="straddle-strip-implied"
              label="IMPLIED 1D"
              value={formatStraddlePct(current.implied_straddle_pct)}
              sub={<>PRIOR CLOSE STRADDLE</>}
            />
            <RegimeStripCell
              testId="straddle-strip-date"
              label="LATEST DATE"
              value={current.date}
              sub={<>DAILY SERIES SINCE 2022-05</>}
            />
            <RegimeStripCell
              testId="straddle-strip-source"
              label="SOURCE UPDATED"
              value={formatSourceDate(vix1dLastModified)}
              sub={<>CBOE VIX1D HISTORY</>}
            />
          </RegimeStrip>
        )}
      </div>

      {/* ── SPX vs straddle ratio chart ───────────────────── */}
      <div className="breadth-history-block" data-testid="straddle-chart-section">
        <HistoryRangeChips
          active={activePreset}
          onChange={(slug) => {
            setCustomRange(null);
            setPreset(slug);
          }}
          maxSessions={total}
          ariaLabel="Straddle chart range"
          dataTestId="straddle-range-chips"
        />

        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="SPX VS 1-DAY STRADDLE RATIO"
          xTickFormat={formatDayTick}
        />
        {total >= 2 && (
          <BrushMinimap
            values={series.map((e) => e.spx_close)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="straddle-brush"
            ariaLabel="Straddle history range brush"
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
