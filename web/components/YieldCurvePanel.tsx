"use client";

import { useEffect, useMemo, useState } from "react";
import { Percent } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import { presetRange, presetSessions, type RangePresetSlug } from "@/lib/historyRange";
import {
  formatDateTick,
  formatEtTime,
  formatSessionDate,
  formatSpreadPct,
  formatYieldPct,
  spreadColor,
} from "@/lib/yieldCurve";
import { useYieldCurve } from "@/lib/useYieldCurve";
import { useYieldCurveLive } from "@/lib/useYieldCurveLive";
import { useViewport } from "@/lib/useViewport";

const SOURCE_FOOTNOTE =
  "Source: US Treasury daily par yield curve (FRBNY 3:30 PM ET quotes). SPX overlay: Yahoo Finance daily closes.";

const INFO_TOOLTIP =
  "Official US Treasury constant-maturity par yields, daily since 1990. The 10Y minus 2Y spread inverts (goes negative) when short rates exceed long rates. Every US recession since 1990 was preceded by an inversion, and downturns have historically begun after the curve re-steepened. Sustained deep inversion is a late-cycle regime marker, not a timing signal.";

interface CurveChartRow {
  date: string;
  spx: number | null;
  spread: number | null;
}

function spreadTone(v: number | null | undefined): "pos" | "neg" | "warn" | "mut" {
  const color = spreadColor(v);
  if (color === "var(--negative)") return "neg";
  if (color === "var(--warning)") return "warn";
  if (color === "var(--positive)") return "pos";
  return "mut";
}

export default function YieldCurvePanel() {
  const { data, loading, syncing, lastSync } = useYieldCurve();
  const { data: live } = useYieldCurveLive();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  // 36-year daily series — default to the full window, unlike the
  // session-based regime charts that default to a recent slice.
  const [activeRange, setActiveRange] = useState<RangePresetSlug | "custom">("all");
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);
  const [rangeTouched, setRangeTouched] = useState(false);

  const series = data?.series ?? [];
  const total = series.length;

  // Never strand a preset the payload can no longer fill — but only while
  // the user hasn't touched the chips themselves.
  useEffect(() => {
    if (rangeTouched || activeRange === "custom" || activeRange === "all") return;
    if (presetSessions(activeRange) > total) setActiveRange("all");
  }, [activeRange, rangeTouched, total]);

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
    return <SpectralLoader label="Loading Treasury yield curve series" />;
  }

  if (!data || data.missing || !data.current || series.length === 0) {
    return (
      <SectionEmptyState
        icon={Percent}
        headline="No yield curve data yet"
        secondary="The yield-curve refresh timer populates this tab from the US Treasury daily par yield feed. Data appears after the first successful pull."
      />
    );
  }

  const current = data.current;
  // Intraday estimate. No live 2Y exists in the IB/UW/Yahoo ladder, so the
  // live cell tracks the 10Y-3M spread flavor (same-source ^TNX and ^IRX);
  // the official EOD 10Y-2Y stays the headline.
  const liveSpread = live && !live.missing ? live.spread_10y_3m : null;

  const rows: CurveChartRow[] = series.map((p) => ({
    date: p.date,
    spx: p.spx_close,
    spread: p.spread,
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<CurveChartRow>, ChartSeries<CurveChartRow>] = [
    {
      key: "spx",
      label: "S&P 500",
      color: chartSeriesColor("primary"),
      axis: "left",
      scaleType: "log",
      format: (v: number) => v.toFixed(0),
    },
    {
      key: "spread",
      label: "SPREAD %",
      color: chartSeriesColor("fault"),
      axis: "right",
      scaleType: "linear",
      format: formatSpreadPct,
    },
  ];

  return (
    <>
      {/* ── Summary strip ─────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Percent size={14} />
            Yield Curve
            <InfoTooltip text={INFO_TOOLTIP} />
          </div>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="yield-curve-mobile-grid">
            <MetricCell
              label="SPREAD"
              value={formatSpreadPct(current.spread)}
              tone={spreadTone(current.spread)}
            />
            <MetricCell label="10Y" value={formatYieldPct(current.y10)} />
            <MetricCell label="2Y" value={formatYieldPct(current.y2)} />
            <MetricCell label="SESSION" value={formatSessionDate(current.date)} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="yield-curve-strip-spread"
              label="SPREAD 10Y-2Y"
              value={
                <span data-testid="yield-curve-spread-value" style={{ color: spreadColor(current.spread) }}>
                  {formatSpreadPct(current.spread)}
                </span>
              }
              sub={<>BELOW ZERO READS AS INVERSION</>}
            />
            <RegimeStripCell
              testId="yield-curve-strip-y10"
              label="10Y"
              value={formatYieldPct(current.y10)}
              sub={<>CONSTANT MATURITY PAR YIELD</>}
            />
            <RegimeStripCell
              testId="yield-curve-strip-y2"
              label="2Y"
              value={formatYieldPct(current.y2)}
              sub={<>CONSTANT MATURITY PAR YIELD</>}
            />
            <RegimeStripCell
              testId="yield-curve-strip-session"
              label="LATEST SESSION"
              value={formatSessionDate(current.date)}
              sub={<>DAILY SERIES SINCE 1990</>}
            />
            <RegimeStripCell
              testId="yield-curve-strip-live"
              label="10Y-3M LIVE"
              value={
                <span
                  data-testid="yield-curve-live-value"
                  style={{ color: liveSpread == null ? "var(--text-muted)" : spreadColor(liveSpread) }}
                >
                  {formatSpreadPct(liveSpread)}
                </span>
              }
              sub={
                liveSpread == null ? (
                  <>ESTIMATE UNAVAILABLE</>
                ) : (
                  <>EST ^TNX MINUS ^IRX · AS OF {formatEtTime(live?.asof)}</>
                )
              }
            />
          </RegimeStrip>
        )}
      </div>

      {/* ── S&P 500 vs 10Y-2Y spread chart ────────────────── */}
      <div className="breadth-history-block" data-testid="yield-curve-chart-section">
        <HistoryRangeChips
          active={activeRange}
          onChange={(preset) => {
            setRangeTouched(true);
            setCustomRange(null);
            setActiveRange(preset);
          }}
          maxSessions={total}
          ariaLabel="Yield curve chart range"
          dataTestId="yield-curve-range-chips"
        />
        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="S&P 500 VS 10Y-2Y TREASURY SPREAD"
          xTickFormat={formatDateTick}
        />
        {total >= 2 && (
          <BrushMinimap
            values={series.map((p) => p.spread ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => {
              setRangeTouched(true);
              setActiveRange("custom");
            }}
            testIdPrefix="yield-curve-brush"
            ariaLabel="Yield curve history range brush"
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
