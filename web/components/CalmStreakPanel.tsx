"use client";

import { useEffect, useMemo, useState } from "react";
import { Waves } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import FreshnessRail from "./FreshnessRail";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import PanelRefreshError from "./PanelRefreshError";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import { presetRange, presetSessions, type RangePresetSlug } from "@/lib/historyRange";
import {
  CALM_STREAK_THRESHOLD_PCT,
  calmStreakStateLabel,
  formatBandPct,
} from "@/lib/calmStreak";
import { CALM_STREAK_REFRESH } from "@/lib/refreshSchedule";
import { useCalmStreak } from "@/lib/useCalmStreak";
import { useViewport } from "@/lib/useViewport";

/**
 * CALM STREAK regime tab. Consecutive SPX sessions whose intraday band
 * (high minus low over the prior close) stayed at or under 1%, with the SPX
 * close on a log overlay. Descriptive only.
 *
 * Spec: docs/indicators/calm-streak.md.
 */

const CALM_STREAK_TOOLTIP =
  "Intraday band = (high - low) / prior close x 100. A session counts toward the streak when its band is not above 1%; a band strictly above 1% resets the streak to zero. " +
  "WINDOW MAX is the longest streak across 1996 to 2016, the comparison window; a current streak above it reads EXTREME CALM. " +
  "Source: Cboe SPX daily OHLC.";

const EMPTY_SECONDARY =
  "The calm-streak refresh timer populates this tab from Cboe SPX daily OHLC. Data appears after the first successful pull.";

interface CalmStreakChartRow {
  date: string;
  close: number | null;
  streak: number | null;
}

function finiteOrNull(v: number | null | undefined): number | null {
  return v != null && Number.isFinite(v) ? v : null;
}

function stateColor(state: string): string {
  if (state === "EXTREME CALM") return "var(--warning)";
  if (state === "CALM") return "var(--positive)";
  return "var(--text-muted)";
}

function toneMobile(color: string): "pos" | "warn" | "mut" {
  if (color === "var(--positive)") return "pos";
  if (color === "var(--warning)") return "warn";
  return "mut";
}

function formatDayTick(d: Date): string {
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit", timeZone: "UTC" });
}

export default function CalmStreakPanel() {
  const { data, loading, syncing, error } = useCalmStreak();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [preset, setPreset] = useState<RangePresetSlug | "custom" | null>(null);
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const series = data?.series ?? [];
  const total = series.length;
  const activePreset: RangePresetSlug | "custom" = preset ?? "all";

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
    return <SpectralLoader label="Loading SPX intraday band series" />;
  }

  if (!data || data.missing || !data.current || !data.stats || series.length === 0) {
    return <SectionEmptyState icon={Waves} headline="No calm streak data yet" secondary={EMPTY_SECONDARY} />;
  }

  const current = data.current;
  const stats = data.stats;
  const percentile = finiteOrNull(stats.percentile);
  const state = calmStreakStateLabel(current.streak, stats.window.streak, percentile ?? 0);
  const color = stateColor(state);
  const percentileText = percentile != null ? `${percentile.toFixed(1)}%` : "---";

  const rows: CalmStreakChartRow[] = series.map((p) => ({
    date: p.date,
    close: finiteOrNull(p.close),
    streak: finiteOrNull(p.streak),
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<CalmStreakChartRow>, ChartSeries<CalmStreakChartRow>] = [
    {
      key: "close",
      label: "SPX",
      color: chartSeriesColor("primary"),
      axis: "left",
      scaleType: "log",
      format: (v: number) => v.toFixed(0),
    },
    {
      key: "streak",
      label: "CALM STREAK",
      color: chartSeriesColor("comparison"),
      axis: "right",
      format: (v: number) => v.toFixed(0),
    },
  ];

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Waves size={14} />
            SPX Calm Streak
            <InfoTooltip text={CALM_STREAK_TOOLTIP} />
          </div>
          <PanelRefreshError error={error} testId="calm-streak-refresh-error" />
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="calm-streak-mobile-grid">
            <MetricCell label="STREAK" value={String(current.streak)} tone={toneMobile(color)} />
            <MetricCell label="STATE" value={state} tone={toneMobile(color)} />
            <MetricCell label="BAND" value={formatBandPct(current.band_pct)} />
            <MetricCell label="WINDOW MAX" value={String(stats.window.streak)} />
            <MetricCell label="RECORD" value={String(stats.max.streak)} />
            <MetricCell label="PERCENTILE" value={percentileText} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="calm-streak-strip-streak"
              label="STREAK"
              value={<span data-testid="calm-streak-value" style={{ color }}>{current.streak}</span>}
              sub={<>SESSIONS AT OR UNDER {CALM_STREAK_THRESHOLD_PCT}%</>}
            />
            <RegimeStripCell
              testId="calm-streak-strip-state"
              label="STATE"
              value={<span data-testid="calm-streak-state" style={{ color, whiteSpace: "nowrap" }}>{state}</span>}
            />
            <RegimeStripCell
              testId="calm-streak-strip-band"
              label="LAST BAND"
              value={<span data-testid="calm-streak-band">{formatBandPct(current.band_pct)}</span>}
              sub={<>{current.date}</>}
            />
            <RegimeStripCell
              testId="calm-streak-strip-window-max"
              label="WINDOW MAX"
              value={<span data-testid="calm-streak-window-max">{stats.window.streak}</span>}
              sub={<>{stats.window.start.slice(0, 4)} - {stats.window.end.slice(0, 4)}</>}
            />
            <RegimeStripCell
              testId="calm-streak-strip-max"
              label="RECORD"
              value={<span data-testid="calm-streak-max">{stats.max.streak}</span>}
              sub={<>{stats.max.date ?? "---"}</>}
            />
            <RegimeStripCell
              testId="calm-streak-strip-percentile"
              label="PERCENTILE"
              value={<span data-testid="calm-streak-percentile">{percentileText}</span>}
            />
          </RegimeStrip>
        )}

        <FreshnessRail
          schedule={CALM_STREAK_REFRESH}
          asOf={data.data_date ?? current.date}
          testId="calm-streak-freshness-rail"
          asOfTestId="calm-streak-strip-asof"
        />
      </div>

      <div className="breadth-history-block" data-testid="calm-streak-chart-section">
        <HistoryRangeChips
          active={activePreset}
          onChange={(slug) => {
            setCustomRange(null);
            setPreset(slug);
          }}
          maxSessions={total}
          ariaLabel="SPX calm streak chart range"
          dataTestId="calm-streak-range-chips"
        />

        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="CONSECUTIVE SESSIONS WITHOUT A >1% INTRADAY BAND"
          xTickFormat={formatDayTick}
        />

        {total >= 2 && (
          <BrushMinimap
            values={series.map((p) => finiteOrNull(p.streak) ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="calm-streak-brush"
            ariaLabel="SPX calm streak history range brush"
          />
        )}
      </div>
    </>
  );
}
