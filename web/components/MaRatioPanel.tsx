"use client";

import { useEffect, useMemo, useState } from "react";
import { Scale } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries, type ReferenceBand } from "./CriHistoryChart";
import FreshnessRail from "./FreshnessRail";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import PanelRefreshError from "./PanelRefreshError";
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
  MA_RATIO_ZONE,
  formatMaPct,
  formatMaRatio,
  formatSpxClose,
  isInSignalZone,
  maRatioStateColor,
  maRatioStateLabel,
  maRatioZoneTurnUp,
  type MaRatioPoint,
} from "@/lib/maRatio";
import { MA_RATIO_REFRESH } from "@/lib/refreshSchedule";
import { useMaRatio } from "@/lib/useMaRatio";
import { useViewport } from "@/lib/useViewport";

/**
 * MA RATIO regime tab. Descriptive read of SPX breadth momentum: the share
 * of members above their 50 day moving average over the share above their
 * 200 day moving average. No validation study was run, so no copy in this
 * file may claim forward information.
 *
 * Spec: docs/indicators/ma-ratio.md.
 */

const MA_RATIO_TOOLTIP =
  "The percent of S&P 500 members closing strictly above their own 50 day moving average, divided by the percent above their 200 day moving average. " +
  "Values near 1.00 mean short term and long term breadth agree; deep washouts print inside the 0.25 to 0.50 zone. " +
  "The buy style signal is the ratio turning up from inside that zone, not merely sitting in it. " +
  "The ratio is null on a full 200 day washout (zero denominator). Computed from constituent closes, not a vendor ratio series.";

const SOURCE_FOOTNOTE =
  "Source: S&P 500 constituent daily closes from the shared member close store, SPX overlay from the ^GSPC series. " +
  "A close exactly on its moving average does not count as above. This is a regime description of breadth momentum, nothing more.";

const EMPTY_SECONDARY =
  "The ma-ratio refresh timer populates this tab from S&P 500 constituent closing prices. Data appears after the first successful sweep.";

interface MaRatioChartRow {
  date: string;
  spx_close: number | null;
  ratio: number | null;
}

function finiteOrNull(v: number | null | undefined): number | null {
  return v != null && Number.isFinite(v) ? v : null;
}

function regimeToneMobile(color: string): "pos" | "neg" | "warn" | "mut" {
  if (color === "var(--positive)") return "pos";
  if (color === "var(--negative)") return "neg";
  if (color === "var(--warning)") return "warn";
  return "mut";
}

/** Daily-series x-tick over a multi-month span: "05 Aug 26". */
function formatDayTick(d: Date): string {
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "2-digit",
    timeZone: "UTC",
  });
}

function formatClockTime(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

export default function MaRatioPanel() {
  const { data, loading, syncing, error } = useMaRatio();
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
    return <SpectralLoader label="Loading SPX moving average breadth series" />;
  }

  if (!data || data.missing || !data.current || series.length === 0) {
    return (
      <SectionEmptyState
        icon={Scale}
        headline="No MA ratio data yet"
        secondary={EMPTY_SECONDARY}
      />
    );
  }

  const current = data.current;
  const ratio = finiteOrNull(current.ratio);
  const state = ratio != null ? maRatioStateLabel(ratio) : null;
  const stateColor = state ? maRatioStateColor(state) : "var(--text-muted)";
  const turnUp = maRatioZoneTurnUp(series as MaRatioPoint[]);
  const signalText = turnUp ? "TURN UP FROM ZONE" : isInSignalZone(ratio) ? "IN ZONE" : "---";
  const signalColor = turnUp ? "var(--positive)" : isInSignalZone(ratio) ? "var(--warning)" : "var(--text-muted)";
  const clock = formatClockTime(data.scan_time);

  const rows: MaRatioChartRow[] = series.map((p) => ({
    date: p.date,
    spx_close: finiteOrNull(p.spx_close),
    ratio: finiteOrNull(p.ratio),
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<MaRatioChartRow>, ChartSeries<MaRatioChartRow>] = [
    {
      key: "spx_close",
      label: "SPX",
      color: chartSeriesColor("primary"),
      axis: "left",
      scaleType: "log",
      format: (v: number) => v.toFixed(0),
    },
    {
      key: "ratio",
      label: "50D/200D RATIO",
      color: chartSeriesColor("comparison"),
      axis: "right",
      format: (v: number) => v.toFixed(2),
    },
  ];

  const zoneBand: ReferenceBand[] = [
    {
      from: data.zone?.low ?? MA_RATIO_ZONE.low,
      to: data.zone?.high ?? MA_RATIO_ZONE.high,
      label: `SIGNAL ZONE ${(data.zone?.low ?? MA_RATIO_ZONE.low).toFixed(2)} - ${(data.zone?.high ?? MA_RATIO_ZONE.high).toFixed(2)}`,
      color: "var(--warning)",
      axis: "right",
    },
  ];

  return (
    <>
      {/* ── Summary strip ─────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Scale size={14} />
            SPX 50D/200D MA Breadth Ratio
            <InfoTooltip text={MA_RATIO_TOOLTIP} />
          </div>
          <PanelRefreshError error={error} testId="ma-ratio-refresh-error" />
          {clock && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {clock}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2 m-regime-grid2x2--fill-last" data-testid="ma-ratio-mobile-grid">
            <MetricCell
              label="RATIO"
              value={formatMaRatio(ratio)}
              tone={regimeToneMobile(stateColor)}
            />
            <MetricCell label="STATE" value={state ?? "---"} tone={regimeToneMobile(stateColor)} />
            <MetricCell label="% > 50D MA" value={formatMaPct(current.pct_above_50)} />
            <MetricCell label="% > 200D MA" value={formatMaPct(current.pct_above_200)} />
            <MetricCell label="SIGNAL" value={signalText} tone={regimeToneMobile(signalColor)} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="ma-ratio-strip-ratio"
              label="RATIO"
              value={
                <span data-testid="ma-ratio-value" style={{ color: stateColor }}>
                  {formatMaRatio(ratio)}
                </span>
              }
              sub={<>PCT50 / PCT200</>}
            />
            <RegimeStripCell
              testId="ma-ratio-strip-state"
              label="STATE"
              value={
                <span data-testid="ma-ratio-state" style={{ color: stateColor, whiteSpace: "nowrap" }}>
                  {state ?? "---"}
                </span>
              }
              sub={<>ZONE {MA_RATIO_ZONE.low.toFixed(2)} - {MA_RATIO_ZONE.high.toFixed(2)}</>}
            />
            <RegimeStripCell
              testId="ma-ratio-strip-pct50"
              label="% > 50D MA"
              value={<span data-testid="ma-ratio-pct50">{formatMaPct(current.pct_above_50)}</span>}
              sub={<>{current.count_above_50} OF {current.eligible_50} MEMBERS</>}
            />
            <RegimeStripCell
              testId="ma-ratio-strip-pct200"
              label="% > 200D MA"
              value={<span data-testid="ma-ratio-pct200">{formatMaPct(current.pct_above_200)}</span>}
              sub={<>{current.count_above_200} OF {current.eligible_200} MEMBERS</>}
            />
            <RegimeStripCell
              testId="ma-ratio-strip-signal"
              label="SIGNAL"
              value={
                <span data-testid="ma-ratio-signal" style={{ color: signalColor, whiteSpace: "nowrap" }}>
                  {signalText}
                </span>
              }
              sub={<>SPX {formatSpxClose(current.spx_close)}</>}
            />
          </RegimeStrip>
        )}

        <FreshnessRail
          schedule={MA_RATIO_REFRESH}
          asOf={data.data_date ?? current.date}
          testId="ma-ratio-freshness-rail"
          asOfTestId="ma-ratio-strip-asof"
        />
      </div>

      {/* ── Ratio over the SPX overlay ────────────────────── */}
      <div className="breadth-history-block" data-testid="ma-ratio-chart-section">
        <HistoryRangeChips
          active={activePreset}
          onChange={(slug) => {
            setCustomRange(null);
            setPreset(slug);
          }}
          maxSessions={total}
          ariaLabel="SPX MA breadth ratio chart range"
          dataTestId="ma-ratio-range-chips"
        />

        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="SPX PCT ABOVE 50D MA / PCT ABOVE 200D MA"
          xTickFormat={formatDayTick}
          referenceBands={zoneBand}
        />

        {total >= 2 && (
          <BrushMinimap
            values={series.map((entry) => finiteOrNull(entry.ratio) ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="ma-ratio-brush"
            ariaLabel="SPX MA breadth ratio history range brush"
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
