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
import { getFreshnessWindowMs, getMarketStateFromDate } from "@/lib/serviceHealthWindows";
import { chartSeriesColor } from "@/lib/chartSystem";
import { presetRange, type RangePresetSlug } from "@/lib/historyRange";
import {
  SOURCE_FOOTNOTE,
  formatIndex,
  formatRatio,
  vixTsRegimeColor,
  vixTsRegimeLabel,
} from "@/lib/vixts";
import { useVixTs } from "@/lib/useVixTs";
import { useViewport } from "@/lib/useViewport";

/**
 * VIX TS regime tab. Spot VIX over 3-month VIX3M, which is the slope of the
 * volatility term structure. This is a descriptive regime read: no validation
 * study was run, so no copy in this file may claim forward information.
 *
 * Spec: docs/indicators/vixts.md section H.
 */

const INFO_TOOLTIP =
  "The ratio of spot VIX to 3-month VIX3M, which is the slope of the volatility term structure. " +
  "Below 1.00 the curve is in contango and near-term volatility is priced below 3-month volatility. " +
  "Above 1.00 it is in backwardation, which means stress has moved into the near term. " +
  "Both legs are official Cboe daily index closes, inner joined on session date back to 2009-09-18. " +
  "Over the full history 7.6 percent of sessions sit at or above 1.00 (BACKWARDATION), 12 percent land in 0.95 to 1.00 (FLAT), " +
  "0.80 to 0.95 is the ordinary CONTANGO state, and under 0.80 is a rare complacency extreme at under 2 percent of sessions. " +
  "Read it as a regime state, not a forecast. Source: CBOE.";

const EMPTY_SECONDARY =
  "The radon-vixts timer populates this tab from the Cboe VIX and VIX3M daily index histories. Data appears after the first successful pull.";

const CHART_TITLE = "VIX TERM STRUCTURE - VIX / VIX3M";

interface VixTsChartRow {
  date: string;
  spx: number | null;
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

/** Daily x-tick over a multi-decade span: "05 Aug 26". */
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
 * snapshot written eight days ago rendered as "2:47 AM" — visually identical
 * to a run that finished this morning. The threshold comes from the same
 * `serviceHealthWindows` entry the watchdog reads, never from a literal, so
 * the panel cannot drift from the catalog. R-365.
 */
function writerAge(
  raw: string | null | undefined,
  now: number = Date.now(),
): { label: string; state: "current" | "behind" | "unknown" } {
  if (!raw) return { label: "---", state: "unknown" };
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return { label: "---", state: "unknown" };

  const ageMs = Math.max(0, now - d.getTime());
  const windowMs = getFreshnessWindowMs("vixts", getMarketStateFromDate(new Date(now)));
  const minutes = Math.floor(ageMs / 60_000);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const magnitude =
    days >= 1 ? `${days}d` : hours >= 1 ? `${hours}h` : `${minutes}m`;

  return {
    label: `${magnitude} ago`,
    state: ageMs > windowMs ? "behind" : "current",
  };
}

export default function VixTsPanel() {
  const { data, loading, syncing } = useVixTs();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  // Daily series back to 2009 — default to the full history so the secular
  // shape of the curve reads at a glance.
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
    return <SpectralLoader label="Loading VIX term structure series" />;
  }

  if (!data || data.missing || !data.current) {
    return (
      <SectionEmptyState
        icon={Activity}
        headline="No VIX term structure data yet"
        secondary={EMPTY_SECONDARY}
      />
    );
  }

  const current = data.current;
  const regime = current.regime ?? vixTsRegimeLabel(current.ratio);
  const regimeColor = vixTsRegimeColor(regime);
  const writer = writerAge(data.scan_time);
  const sourceSession = data.data_date ?? current.date ?? "---";

  const rows: VixTsChartRow[] = series.map((p) => ({
    date: p.date,
    spx: finiteOrNull(p.spx),
    ratio: finiteOrNull(p.ratio),
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<VixTsChartRow>, ChartSeries<VixTsChartRow>] = [
    {
      key: "spx",
      label: "SPX",
      color: chartSeriesColor("primary"),
      axis: "left",
      // Multi-decade price: a log scale keeps the 2009 tail legible.
      scaleType: "log",
      format: (v: number) => v.toFixed(0),
    },
    {
      key: "ratio",
      label: "VIX / VIX3M",
      color: chartSeriesColor("comparison"),
      axis: "right",
      scaleType: "linear",
      format: (v: number) => v.toFixed(2),
    },
  ];

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Activity size={14} />
            VIX Term Structure
            <InfoTooltip text={INFO_TOOLTIP} />
          </div>
          <span
            data-testid="vixts-writer-age"
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
          <div className="m-regime-grid2x2" data-testid="vixts-mobile-grid">
            <MetricCell label="RATIO" value={formatRatio(current.ratio)} />
            <MetricCell label="REGIME" value={regime} tone={regimeToneMobile(regimeColor)} />
            <MetricCell label="VIX" value={formatIndex(current.vix)} />
            <MetricCell label="VIX 3M" value={formatIndex(current.vix3m)} />
            <MetricCell label="SOURCE UPDATED" value={sourceSession} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="vixts-strip-ratio"
              label="RATIO"
              value={<span data-testid="vixts-ratio">{formatRatio(current.ratio)}</span>}
              sub={<>VIX / VIX3M</>}
            />
            <RegimeStripCell
              testId="vixts-strip-regime"
              label="REGIME"
              value={
                <span data-testid="vixts-regime" style={{ color: regimeColor }}>
                  {regime}
                </span>
              }
              sub={<>BANDS 0.80 / 0.95 / 1.00</>}
            />
            <RegimeStripCell
              testId="vixts-strip-vix"
              label="VIX"
              value={<span data-testid="vixts-vix">{formatIndex(current.vix)}</span>}
              sub={<>30-DAY IMPLIED VOLATILITY</>}
            />
            <RegimeStripCell
              testId="vixts-strip-vix3m"
              label="VIX 3M"
              value={<span data-testid="vixts-vix3m">{formatIndex(current.vix3m)}</span>}
              sub={<>3-MONTH IMPLIED VOLATILITY</>}
            />
            <RegimeStripCell
              testId="vixts-strip-source-updated"
              label="SOURCE UPDATED"
              value={<span data-testid="vixts-source-updated">{sourceSession}</span>}
              sub={<>LATEST SESSION IN THE CBOE FILE</>}
            />
          </RegimeStrip>
        )}
      </div>

      <div className="breadth-history-block" data-testid="vixts-chart-section">
        <HistoryRangeChips
          active={activeRange}
          onChange={(next) => {
            setCustomRange(null);
            setActiveRange(next);
          }}
          maxSessions={total}
          ariaLabel="VIX term structure chart range"
          dataTestId="vixts-range-chips"
        />
        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title={CHART_TITLE}
          xTickFormat={formatDayTick}
        />
        {total >= 2 && (
          <BrushMinimap
            values={rows.map((p) => p.ratio ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setActiveRange("custom")}
            testIdPrefix="vixts-brush"
            ariaLabel="VIX term structure history range brush"
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
