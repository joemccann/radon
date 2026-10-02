"use client";

import { useEffect, useMemo, useState } from "react";
import { Percent } from "lucide-react";
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
  RSI_OVERSOLD_THRESHOLD,
  formatRsiPct,
  formatSpxClose,
  rsiOversoldStateColor,
  rsiOversoldStateLabel,
  type RsiOversoldPoint,
} from "@/lib/rsiOversold";
import { RSI_OVERSOLD_REFRESH } from "@/lib/refreshSchedule";
import { useRsiOversold } from "@/lib/useRsiOversold";
import { useViewport } from "@/lib/useViewport";

/**
 * RSI OVERSOLD regime tab. Descriptive read of SPX short-term oversold
 * breadth: the share of members whose own 14-day Wilder RSI closed below 30.
 * No validation study is shown, so no copy in this file may claim forward
 * information.
 *
 * Spec: docs/indicators/rsi-oversold.md.
 */

const RSI_OVERSOLD_TOOLTIP =
  "The percent of current S&P 500 members whose own 14-day Wilder RSI closed strictly below 30. " +
  "A reading above 10% is an oversold cluster. A close with RSI exactly 30 does not count. " +
  "Current membership is applied to all history (survivorship bias). " +
  "This is a regime description of short-term oversold breadth, nothing more.";

const SOURCE_FOOTNOTE =
  "Source: S&P 500 constituent daily closes from the shared member close store, SPX overlay from the ^GSPC series. " +
  "Split-adjusted, dividend-unadjusted closes. Current membership applied to all history. " +
  "This is a regime description of short-term oversold breadth, nothing more.";

const EMPTY_SECONDARY =
  "The rsi-oversold refresh timer populates this tab from S&P 500 constituent closing prices. Data appears after the first successful sweep.";

interface RsiOversoldChartRow {
  date: string;
  spx_close: number | null;
  pct_below_30: number | null;
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

export default function RsiOversoldPanel() {
  const { data, loading, syncing, error } = useRsiOversold();
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
    return <SpectralLoader label="Loading SPX RSI oversold breadth series" />;
  }

  if (!data || data.missing || !data.current || series.length === 0) {
    return (
      <SectionEmptyState
        icon={Percent}
        headline="No RSI oversold data yet"
        secondary={EMPTY_SECONDARY}
      />
    );
  }

  const current = data.current;
  const pct = finiteOrNull(current.pct_below_30);
  const state = pct != null ? rsiOversoldStateLabel(pct) : null;
  const stateColor = state ? rsiOversoldStateColor(state) : "var(--text-muted)";
  const threshold = data.threshold ?? RSI_OVERSOLD_THRESHOLD;
  const clock = formatClockTime(data.scan_time);

  const rows: RsiOversoldChartRow[] = series.map((p) => ({
    date: p.date,
    spx_close: finiteOrNull(p.spx_close),
    pct_below_30: finiteOrNull(p.pct_below_30),
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<RsiOversoldChartRow>, ChartSeries<RsiOversoldChartRow>] = [
    {
      key: "spx_close",
      label: "SPX",
      color: chartSeriesColor("primary"),
      axis: "left",
      scaleType: "log",
      format: (v: number) => v.toFixed(0),
    },
    {
      key: "pct_below_30",
      label: "% RSI(14) < 30",
      color: chartSeriesColor("comparison"),
      axis: "right",
      format: (v: number) => `${v.toFixed(1)}%`,
    },
  ];

  const thresholdBand: ReferenceBand[] = [
    {
      from: threshold,
      to: threshold,
      label: "10% oversold cluster threshold",
      color: "var(--warning)",
      axis: "right",
    },
  ];

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Percent size={14} />
            SPX RSI(14) Oversold Breadth
            <InfoTooltip text={RSI_OVERSOLD_TOOLTIP} />
          </div>
          <PanelRefreshError error={error} testId="rsi-oversold-refresh-error" />
          {clock && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-meta)", color: "var(--text-muted)" }}>
              {clock}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2 m-regime-grid2x2--fill-last" data-testid="rsi-oversold-mobile-grid">
            <MetricCell
              label="PCT < 30"
              value={formatRsiPct(pct)}
              tone={regimeToneMobile(stateColor)}
            />
            <MetricCell label="STATE" value={state ?? "---"} tone={regimeToneMobile(stateColor)} />
            <MetricCell label="MEMBERS" value={`${current.count_below_30} / ${current.eligible}`} />
            <MetricCell label="HIGHEST SINCE" value={current.highest_since ?? "---"} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="rsi-oversold-strip-pct"
              label="PCT < 30"
              value={
                <span data-testid="rsi-oversold-value" style={{ color: stateColor }}>
                  {formatRsiPct(pct)}
                </span>
              }
              sub={<>RSI(14) STRICTLY BELOW 30</>}
            />
            <RegimeStripCell
              testId="rsi-oversold-strip-state"
              label="STATE"
              value={
                <span data-testid="rsi-oversold-state" style={{ color: stateColor, whiteSpace: "nowrap" }}>
                  {state ?? "---"}
                </span>
              }
              sub={<>THRESHOLD {threshold.toFixed(0)}%</>}
            />
            <RegimeStripCell
              testId="rsi-oversold-strip-members"
              label="MEMBERS"
              value={
                <span data-testid="rsi-oversold-members">
                  {current.count_below_30} / {current.eligible}
                </span>
              }
              sub={<>BELOW 30 / ELIGIBLE</>}
            />
            <RegimeStripCell
              testId="rsi-oversold-strip-highest"
              label="HIGHEST SINCE"
              value={
                <span data-testid="rsi-oversold-highest">
                  {current.highest_since ?? "---"}
                </span>
              }
              sub={<>PRIOR SESSION AT OR ABOVE LATEST</>}
            />
            <RegimeStripCell
              testId="rsi-oversold-strip-spx"
              label="SPX CLOSE"
              value={<span data-testid="rsi-oversold-spx">{formatSpxClose(current.spx_close)}</span>}
              sub={<>{current.cross_up ? "CROSS UP" : "NO CROSS"}</>}
            />
          </RegimeStrip>
        )}

        <FreshnessRail
          schedule={RSI_OVERSOLD_REFRESH}
          asOf={data.data_date ?? current.date}
          testId="rsi-oversold-freshness-rail"
          asOfTestId="rsi-oversold-strip-asof"
        />
      </div>

      <div className="breadth-history-block" data-testid="rsi-oversold-chart-section">
        <HistoryRangeChips
          active={activePreset}
          onChange={(slug) => {
            setCustomRange(null);
            setPreset(slug);
          }}
          maxSessions={total}
          ariaLabel="SPX RSI oversold breadth chart range"
          dataTestId="rsi-oversold-range-chips"
        />

        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="SPX PCT OF MEMBERS WITH RSI(14) BELOW 30"
          xTickFormat={formatDayTick}
          referenceBands={thresholdBand}
        />

        {total >= 2 && (
          <BrushMinimap
            values={series.map((entry: RsiOversoldPoint) => finiteOrNull(entry.pct_below_30) ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="rsi-oversold-brush"
            ariaLabel="SPX RSI oversold breadth history range brush"
          />
        )}

        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "var(--text-meta)",
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
