"use client";

import type { CSSProperties } from "react";

import { useMemo, useState } from "react";
import { Activity } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries, type ReferenceLevel } from "./CriHistoryChart";
import FreshnessRail from "./FreshnessRail";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { getFreshnessWindowMs, getMarketStateFromDate } from "@/lib/serviceHealthWindows";
import { VIX_PC_REFRESH } from "@/lib/refreshSchedule";
import { chartSeriesColor } from "@/lib/chartSystem";
import { presetRange, type RangePresetSlug } from "@/lib/historyRange";
import {
  buildVixPcChartRows,
  formatPctile,
  formatRatio,
  vixPcRegime,
  vixPcRegimeColor,
  type VixPcChartRow,
} from "@/lib/vixPc";
import { useVixPc } from "@/lib/useVixPc";
import { useViewport } from "@/lib/useViewport";

/**
 * VIX P/C regime tab. Five-session cumulative VIX put/call ratio. This is a
 * descriptive regime read: no validation study was run, so no copy in this
 * file may claim forward information.
 *
 * Spec: docs/indicators/vix-pc.md sections A, C.4, G.2.
 */

const INFO_TOOLTIP =
  "The five-session cumulative VIX put/call ratio, which is the sum of " +
  "VIX put volume over the last five sessions divided by the sum of VIX " +
  "call volume over those same sessions. A low reading means call volume " +
  "dominates and the VIX options tape is more cautious. A high reading " +
  "means put volume dominates. Below the 10th percentile of stored history " +
  "is CAUTIOUS. From 10 up to 90 is NORMAL. At or above 90 is COMPLACENT. " +
  "Read it as a regime state of VIX options activity. Source: Unusual Whales.";

const EMPTY_SECONDARY =
  "The vix-pc refresh timer populates this tab from the UW VIX options-volume history. Data appears after the first successful pull.";

const SOURCE_FOOTNOTE =
  "Source: Unusual Whales VIX options volume. Cumulative window is five sessions, inclusive of the session. This is a regime description of VIX options activity, nothing more.";

const CHART_TITLE = "VIX 5S PUT/CALL";

function finiteOrNull(v: number | null | undefined): number | null {
  return v != null && Number.isFinite(v) ? v : null;
}

function regimeToneMobile(color: string): "pos" | "neg" | "warn" | "mut" {
  if (color === "var(--positive)") return "pos";
  if (color === "var(--negative)") return "neg";
  if (color === "var(--warning)") return "warn";
  return "mut";
}

/** Daily x-tick over a multi-year span: "05 Aug 26". */
function formatDayTick(d: Date): string {
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "2-digit",
    timeZone: "UTC",
  });
}

/** Writer freshness as an AGE, plus a verdict against the shared catalog.
 *
 * A bare hour:minute was the panel's only writer-freshness signal, so a
 * snapshot written eight days ago rendered as "2:47 AM" - visually identical
 * to a run that finished this morning. The threshold comes from the same
 * `serviceHealthWindows` entry the watchdog reads, never from a literal, so
 * the panel cannot drift from the catalog.
 */
function writerAge(
  raw: string | null | undefined,
  now: number = Date.now(),
): { label: string; state: "current" | "behind" | "unknown" } {
  if (!raw) return { label: "---", state: "unknown" };
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return { label: "---", state: "unknown" };

  const ageMs = Math.max(0, now - d.getTime());
  const windowMs = getFreshnessWindowMs("vix-pc", getMarketStateFromDate(new Date(now)));
  const minutes = Math.floor(ageMs / 60_000);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const magnitude =
    days >= 1 ? `${days}d` : hours >= 1 ? `${hours}h` : `${minutes}m`;

  return {
    label: `PULLED ${magnitude} ago`,
    state: ageMs > windowMs ? "behind" : "current",
  };
}

export default function VixPcPanel() {
  const { data, loading, syncing } = useVixPc();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [activeRange, setActiveRange] = useState<RangePresetSlug | "custom">("all");
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const series = data?.series ?? [];
  const total = series.length;

  const chartRange = useMemo<[number, number]>(() => {
    if (total < 2) return [0, Math.max(total - 1, 0)];
    if (activeRange === "custom" && customRange) {
      const max = total - 1;
      const end = Math.min(customRange[1], max);
      const start = Math.max(0, Math.min(customRange[0], end));
      return [start, end];
    }
    return presetRange(activeRange === "custom" ? "all" : activeRange, total);
  }, [activeRange, customRange, total]);

  if ((loading || syncing) && !data) {
    return <SpectralLoader label="Loading VIX put/call series" />;
  }

  if (!data || data.missing || !data.current) {
    return (
      <SectionEmptyState
        icon={Activity}
        headline="No VIX put/call data yet"
        secondary={EMPTY_SECONDARY}
      />
    );
  }

  const current = data.current;
  const regime = current.regime ?? vixPcRegime(current.percentile);
  const regimeColor = vixPcRegimeColor(regime);
  const headlineTone = regimeColor === "var(--text-muted)" ? "var(--text-primary)" : regimeColor;
  const writer = writerAge(data.scan_time);

  const rows = buildVixPcChartRows(series);
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<VixPcChartRow>] = [
    {
      key: "cum5_ratio",
      label: "5S P/C",
      color: chartSeriesColor("primary"),
      axis: "left",
      scaleType: "linear",
      format: (v: number) => v.toFixed(3),
    },
  ];

  const referenceLevels: ReferenceLevel[] = [];
  const seriesMin = finiteOrNull(current.series_min);
  const seriesP10 = finiteOrNull(current.series_p10);
  const seriesP90 = finiteOrNull(current.series_p90);
  if (seriesMin != null) {
    referenceLevels.push({ value: seriesMin, label: "MIN", color: "var(--warning)" });
  }
  if (seriesP10 != null) {
    referenceLevels.push({ value: seriesP10, label: "P10", color: "var(--text-muted)" });
  }
  if (seriesP90 != null) {
    referenceLevels.push({ value: seriesP90, label: "P90", color: "var(--text-muted)" });
  }

  const seriesLowText = current.series_min_date
    ? `${formatRatio(current.series_min)} ${current.series_min_date}`
    : formatRatio(current.series_min);

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Activity size={14} />
            VIX 5-session put/call
            <InfoTooltip text={INFO_TOOLTIP} />
          </div>
          <span
            data-testid="vix-pc-writer-age"
            data-state={writer.state}
            title={data.scan_time ? `Writer last wrote ${data.scan_time}` : "No writer timestamp"}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "var(--text-meta)",
              color: writer.state === "behind" ? "var(--warning)" : "var(--text-muted)",
            }}
          >
            {writer.label}
          </span>
        </div>

        {compact ? (
          <div className="m-regime-grid2x2 m-regime-grid2x2--fill-last" data-testid="vix-pc-mobile-grid">
            <MetricCell label="5S P/C" value={formatRatio(current.cum5_ratio)} tone={regimeToneMobile(regimeColor)} testId="vix-pc-cum5" />
            <MetricCell label="PCTILE" value={formatPctile(current.percentile)} testId="vix-pc-percentile" />
            <MetricCell label="REGIME" value={regime ?? "---"} tone={regimeToneMobile(regimeColor)} testId="vix-pc-regime" />
            <MetricCell label="DAILY P/C" value={formatRatio(current.daily_ratio)} testId="vix-pc-daily" />
            <MetricCell label="SERIES LOW" value={seriesLowText} testId="vix-pc-series-low" />
          </div>
        ) : (
          <div className="regime-strip-fit" style={{ "--fit-cols": 5, "--fit-cols-md": 3 } as CSSProperties}>
            <RegimeStrip>
              <RegimeStripCell
                testId="vix-pc-strip-cum5"
                label="5S P/C"
                value={
                  <span data-testid="vix-pc-cum5" style={{ color: headlineTone }}>
                    {formatRatio(current.cum5_ratio)}
                  </span>
                }
                sub={<>FIVE-SESSION CUMULATIVE</>}
              />
              <RegimeStripCell
                testId="vix-pc-strip-percentile"
                label="PCTILE"
                value={<span data-testid="vix-pc-percentile">{formatPctile(current.percentile)}</span>}
                sub={<>SHARE OF SESSIONS BELOW</>}
              />
              <RegimeStripCell
                testId="vix-pc-strip-regime"
                label="REGIME"
                value={
                  <span data-testid="vix-pc-regime" style={{ color: headlineTone, whiteSpace: "nowrap" }}>
                    {regime ?? "---"}
                  </span>
                }
                sub={<>BANDS 10 / 90</>}
              />
              <RegimeStripCell
                testId="vix-pc-strip-daily"
                label="DAILY P/C"
                value={<span data-testid="vix-pc-daily">{formatRatio(current.daily_ratio)}</span>}
                sub={<>SINGLE SESSION PUT / CALL</>}
              />
              <RegimeStripCell
                testId="vix-pc-strip-series-low"
                label="SERIES LOW"
                value={<span data-testid="vix-pc-series-low">{seriesLowText}</span>}
                sub={<>MINIMUM CUM5 IN HISTORY</>}
              />
            </RegimeStrip>
          </div>
        )}

        <FreshnessRail
          schedule={VIX_PC_REFRESH}
          asOf={data.data_date ?? current.date}
          testId="vix-pc-freshness-rail"
          asOfTestId="vix-pc-strip-asof"
        />
      </div>

      <div className="breadth-history-block" data-testid="vix-pc-chart-section">
        <HistoryRangeChips
          active={activeRange}
          onChange={(next) => {
            setCustomRange(null);
            setActiveRange(next);
          }}
          maxSessions={total}
          ariaLabel="VIX put/call chart range"
          dataTestId="vix-pc-range-chips"
        />
        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title={CHART_TITLE}
          xTickFormat={formatDayTick}
          referenceLevels={referenceLevels.length > 0 ? referenceLevels : undefined}
        />
        {total >= 2 && (
          <BrushMinimap
            values={rows.map((p) => p.cum5_ratio ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setActiveRange("custom")}
            testIdPrefix="vix-pc-brush"
            ariaLabel="VIX put/call history range brush"
          />
        )}
        <div className="section-footnote">
          {SOURCE_FOOTNOTE}
        </div>
      </div>
    </>
  );
}
