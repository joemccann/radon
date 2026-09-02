"use client";

import { useEffect, useMemo, useState } from "react";
import { Diff } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import PanelRefreshError from "./PanelRefreshError";
import CriHistoryChart, {
  type ChartSeries,
  type ReferenceBand,
  type ReferenceLevel,
} from "./CriHistoryChart";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import FreshnessRail from "./FreshnessRail";
import { IV_SPREAD_REFRESH } from "@/lib/refreshSchedule";
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
  buildIvSpreadChartRows,
  formatIvPercent,
  formatSpread,
  formatZ,
  ivSpreadRegime,
  ivSpreadRegimeColor,
  type IvSpreadChartRow,
} from "@/lib/ivSpread";
import { useIvSpread } from "@/lib/useIvSpread";
import { useViewport } from "@/lib/useViewport";

/**
 * IV SPREAD regime tab. Descriptive read of the premium the market pays for
 * NDX 1M optionality over SPX 1M optionality, against its own full-history
 * mean and standard deviation, and nothing more. No validation study was
 * run, so no copy in this file may claim forward information.
 *
 * Spec: docs/indicators/iv-spread.md sections A, B.4, F.3, G.3.
 */

const IV_SPREAD_TOOLTIP =
  "NDX 30-day implied volatility minus SPX 30-day implied volatility, in volatility points. A wide spread means the market is paying up for tech optionality relative to the broad index; a thin or negative spread means that premium has been bid away. Read against its own five-year mean and standard deviation, nothing more.";

const SOURCE_FOOTNOTE =
  "Source: Interactive Brokers NDX and SPX 30-day ATM implied volatility daily closes. Spread in volatility points against its full stored history. This is a regime description of relative option premium, nothing more.";

const EMPTY_SECONDARY =
  "The iv-spread refresh timer populates this tab from the NDX and SPX 30-day implied volatility histories. Data appears after the first successful pull.";

/** 0-100 payload percent to one decimal: 59.377494 -> "59.4%"; "---" unavailable. */
function formatPctPoints(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v.toFixed(1)}%`;
}

function finiteOrNull(v: number | null | undefined): number | null {
  return v != null && Number.isFinite(v) ? v : null;
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

export default function IvSpreadPanel() {
  const { data, loading, syncing, error } = useIvSpread();
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
    return <SpectralLoader label="Loading NDX vs SPX IV spread series" />;
  }

  if (!data || data.missing || !data.current || series.length === 0) {
    return (
      <SectionEmptyState
        icon={Diff}
        headline="No NDX vs SPX IV spread data yet"
        secondary={EMPTY_SECONDARY}
      />
    );
  }

  const current = data.current;
  const stats = data.stats;
  const regime = ivSpreadRegime(current.z_score) ?? current.regime;
  const regimeTone = ivSpreadRegimeColor(regime);
  const mean = finiteOrNull(stats?.mean);
  const stdev = finiteOrNull(stats?.stdev);
  const zSub =
    stats && stats.count != null && mean != null
      ? `VS ${stats.count}-SESSION MEAN ${formatSpread(mean)}`
      : "VS HISTORY";

  const rows = buildIvSpreadChartRows(series);
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  // The spread sits on the LEFT scale: CriHistoryChart draws referenceLevels
  // on the left axis, and the AVG guide must share the spread's scale.
  const chartSeries: [ChartSeries<IvSpreadChartRow>, ChartSeries<IvSpreadChartRow>] = [
    {
      key: "spread",
      label: "SPREAD",
      color: chartSeriesColor("primary"),
      axis: "left",
      format: (v: number) => v.toFixed(2),
    },
    {
      key: "spx_iv",
      label: "SPX 1M IV",
      color: chartSeriesColor("comparison"),
      axis: "right",
      format: (v: number) => `${(v * 100).toFixed(1)}%`,
    },
  ];

  const referenceLevels: ReferenceLevel[] | undefined =
    mean != null ? [{ value: mean, label: `AVG ${formatSpread(mean)}` }] : undefined;
  const referenceBands: ReferenceBand[] | undefined =
    mean != null && stdev != null
      ? [{ from: mean - stdev, to: mean + stdev, label: "1 SD", axis: "left" }]
      : undefined;

  // An IB outage re-serves the PREVIOUS payload with a fresh `scan_time`
  // (fetch_iv_spread._serve_cached), so the clock would tick over a reading
  // that has not moved. Surface it as STALE and show the payload date instead.
  const degraded = data.status === "stale_source";

  const clock = degraded
    ? data.as_of
    : data.scan_time
      ? new Date(data.scan_time).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })
      : null;

  const statsLine = stats
    ? [
        `HIGH ${formatSpread(stats.high)} (${stats.high_date ?? "---"})`,
        `LOW ${formatSpread(stats.low)} (${stats.low_date ?? "---"})`,
        `AVG ${formatSpread(stats.mean)}`,
        `LAST ${formatSpread(stats.last)}`,
        `STDEV ${formatSpread(stats.stdev)}`,
      ].join("  ")
    : "HIGH --- LOW --- AVG --- LAST --- STDEV ---";

  return (
    <>
      {/* ── Summary strip ─────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Diff size={14} />
            NDX vs SPX 1M IV Spread
            <InfoTooltip text={IV_SPREAD_TOOLTIP} />
          </div>
          <PanelRefreshError error={error} testId="iv-spread-refresh-error" />
          {degraded && (
            <span
              data-testid="iv-spread-degraded"
              title="IB did not answer; showing the last good reading."
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "9px",
                letterSpacing: "0.08em",
                color: "var(--warning)",
                border: "1px solid var(--warning)",
                padding: "2px 5px",
                textTransform: "uppercase",
                lineHeight: 1,
                borderRadius: "999px",
              }}
            >
              STALE
            </span>
          )}
          {clock && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {clock}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="iv-spread-mobile-grid">
            <MetricCell label="SPREAD" value={formatSpread(current.spread)} />
            <MetricCell label="REGIME" value={regime ?? "---"} />
            <MetricCell label="NDX 1M IV" value={formatIvPercent(current.ndx_iv)} />
            <MetricCell label="SPX 1M IV" value={formatIvPercent(current.spx_iv)} />
            <MetricCell label="Z-SCORE" value={formatZ(current.z_score)} />
            <MetricCell label="PCTILE" value={formatPctPoints(current.pctile)} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="iv-spread-strip-spread"
              label="SPREAD"
              value={
                <span data-testid="iv-spread-spread-value" style={{ color: regimeTone }}>
                  {formatSpread(current.spread)}
                </span>
              }
              sub={<>1D {formatSpread(current.change_1d)} VOL PTS</>}
            />
            <RegimeStripCell
              testId="iv-spread-strip-ndx"
              label="NDX 1M IV"
              value={formatIvPercent(current.ndx_iv)}
              sub={<>30D ATM IMPLIED VOL</>}
            />
            <RegimeStripCell
              testId="iv-spread-strip-spx"
              label="SPX 1M IV"
              value={formatIvPercent(current.spx_iv)}
              sub={<>30D ATM IMPLIED VOL</>}
            />
            <RegimeStripCell
              testId="iv-spread-strip-z"
              label="Z-SCORE"
              value={formatZ(current.z_score)}
              sub={<>{zSub}</>}
            />
            <RegimeStripCell
              testId="iv-spread-strip-pctile"
              label="PCTILE"
              value={formatPctPoints(current.pctile)}
              sub={<>SHARE OF SESSIONS BELOW</>}
            />
            <RegimeStripCell
              testId="iv-spread-strip-regime"
              label="REGIME"
              value={
                <span
                  data-testid="iv-spread-regime-value"
                  style={{ color: regimeTone, whiteSpace: "nowrap" }}
                >
                  {regime ?? "---"}
                </span>
              }
              sub={<>TECH VOL PREMIUM VS MEAN</>}
            />
          </RegimeStrip>
        )}

        <FreshnessRail
          schedule={IV_SPREAD_REFRESH}
          asOf={data.as_of ?? current.date}
          testId="iv-spread-freshness-rail"
          asOfTestId="iv-spread-strip-asof"
        />
      </div>

      {/* ── Spread over the SPX 1M IV series ──────────────── */}
      <div className="breadth-history-block" data-testid="iv-spread-chart-section">
        <HistoryRangeChips
          active={activePreset}
          onChange={(slug) => {
            setCustomRange(null);
            setPreset(slug);
          }}
          maxSessions={total}
          ariaLabel="NDX vs SPX IV spread chart range"
          dataTestId="iv-spread-range-chips"
        />

        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="NDX VS SPX 1M ATM IMPLIED VOL SPREAD"
          xTickFormat={formatDayTick}
          referenceLevels={referenceLevels}
          referenceBands={referenceBands}
        />

        {total >= 2 && (
          <BrushMinimap
            values={series.map((entry) => entry.spread)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="iv-spread-brush"
            ariaLabel="NDX vs SPX IV spread history range brush"
          />
        )}

        <div
          data-testid="iv-spread-stats"
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "9px",
            color: "var(--text-muted)",
            marginTop: "8px",
            whiteSpace: "pre-wrap",
          }}
        >
          {statsLine}
        </div>

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
