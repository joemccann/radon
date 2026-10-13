"use client";

import { useEffect, useMemo, useState } from "react";
import { Scale } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import PanelRefreshError from "./PanelRefreshError";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import FreshnessRail from "./FreshnessRail";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import { formatDateTick, formatSessionDate } from "@/lib/creditSpread";
import {
  defaultPresetForLength,
  presetRange,
  presetSessions,
  type RangePresetSlug,
} from "@/lib/historyRange";
import { IEI_HYG_REFRESH } from "@/lib/refreshSchedule";
import { formatRatio, stateLabel, stateTone, type IeiHygState } from "@/lib/ieiHyg";
import { useIeiHyg } from "@/lib/useIeiHyg";
import { useViewport } from "@/lib/useViewport";

const INFO_TOOLTIP =
  "The price ratio of IEI over HYG. IEI is the iShares 3-7 Year Treasury Bond ETF: coupon-paying US Treasury notes in the middle of the curve (3 to 7 year maturities, no STRIPS), a pure intermediate rates instrument. HYG is the iShares iBoxx USD High Yield Corporate Bond ETF: the most liquid corner of the junk bond market, typically shorter maturity and less rate sensitive than the broad junk universe. A new 52-week low in the ratio means high yield credit is outperforming Treasuries: spreads tightening, risk-on. A new 52-week high means money is leaving junk credit for Treasuries: risk-off. DXY is an overlay only.";

interface IeiHygChartRow {
  date: string;
  ratio: number | null;
  dxy: number | null;
}

const TONE_COLOR = {
  positive: "var(--positive)",
  negative: "var(--negative)",
  muted: "var(--text-muted)",
} as const;

const TONE_MOBILE = { positive: "pos", negative: "neg", muted: "mut" } as const;

function stateColor(state: IeiHygState | null | undefined): string {
  return TONE_COLOR[stateTone(state)];
}

function formatRank(pct: number | null | undefined): string {
  if (pct == null || !Number.isFinite(pct)) return "---";
  return `${Math.round(pct * 100)}%`;
}

function formatDxy(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}

/** "2026-09-18" -> "Sep 18". The 52W window makes the year implicit. */
function formatShortDate(raw: string | null | undefined): string {
  if (!raw) return "---";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

function finiteOrNull(v: number | null | undefined): number | null {
  return v != null && Number.isFinite(v) ? v : null;
}

export default function IeiHygPanel() {
  const { data, loading, syncing, lastSync, error } = useIeiHyg();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [preset, setPreset] = useState<RangePresetSlug | "custom" | null>(null);
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const series = data?.series ?? [];
  const total = series.length;
  const activeRange: RangePresetSlug | "custom" = preset ?? defaultPresetForLength(total);

  useEffect(() => {
    if (preset === null || preset === "custom" || preset === "all") return;
    if (presetSessions(preset) > total) setPreset("all");
  }, [preset, total]);

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
    return <SpectralLoader label="Loading Treasury vs high yield ratio series" />;
  }

  if (!data || data.missing || !data.current || series.length === 0) {
    return (
      <SectionEmptyState
        icon={Scale}
        headline="No Treasury vs high yield snapshot"
        secondary="Waiting for the iei-hyg refresh timer"
      />
    );
  }

  const current = data.current;
  const rows: IeiHygChartRow[] = series.map((p) => ({
    date: p.date,
    ratio: finiteOrNull(p.ratio),
    dxy: finiteOrNull(p.dxy_close),
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<IeiHygChartRow>, ChartSeries<IeiHygChartRow>] = [
    {
      key: "dxy",
      label: "DXY",
      color: chartSeriesColor("fault"),
      axis: "left",
      scaleType: "linear",
      format: (v: number) => v.toFixed(2),
    },
    {
      key: "ratio",
      label: "IEI / HYG",
      color: chartSeriesColor("primary"),
      axis: "right",
      scaleType: "linear",
      format: (v: number) => v.toFixed(4),
    },
  ];

  const sourceText = `${(data.source ?? "---").toUpperCase()} ${formatSessionDate(current.date)}`;
  // Linear position of the ratio inside its own 52W low-high span (0 = low, 100 = high).
  const rangeSpan = current.ratio_52w_high - current.ratio_52w_low;
  const rangePos =
    Number.isFinite(current.ratio) && Number.isFinite(rangeSpan) && rangeSpan > 0
      ? Math.min(100, Math.max(0, ((current.ratio - current.ratio_52w_low) / rangeSpan) * 100))
      : null;

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Scale size={14} />
            Treasuries vs High Yield
            <InfoTooltip text={INFO_TOOLTIP} />
          </div>
          <PanelRefreshError error={error} testId="iei-hyg-refresh-error" />
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-meta)", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="iei-hyg-mobile-grid">
            <MetricCell label="RATIO" value={formatRatio(current.ratio)} />
            <MetricCell
              label="STATE"
              value={stateLabel(current.state)}
              tone={TONE_MOBILE[stateTone(current.state)]}
            />
            <MetricCell label="52W LOW" value={formatRatio(current.ratio_52w_low)} />
            <MetricCell label="52W HIGH" value={formatRatio(current.ratio_52w_high)} />
            <MetricCell label="RANK" value={formatRank(current.ratio_pct_rank)} />
            <MetricCell label="DXY" value={formatDxy(current.dxy_close)} />
            <MetricCell label="SOURCE" value={sourceText} />
          </div>
        ) : (
          <div className="iei-hyg-strip">
          <RegimeStrip>
            <RegimeStripCell
              testId="iei-hyg-strip-ratio"
              label="RATIO"
              value={<span data-testid="iei-hyg-ratio">{formatRatio(current.ratio)}</span>}
              sub={<>IEI 3-7Y TSY / HYG HY CORP · RANK <span data-testid="iei-hyg-rank">{formatRank(current.ratio_pct_rank)}</span></>}
            />
            <RegimeStripCell
              testId="iei-hyg-strip-state"
              label="STATE"
              value={
                <span data-testid="iei-hyg-state" style={{ color: stateColor(current.state) }}>
                  {stateLabel(current.state)}
                </span>
              }
              sub={<>{current.window_sessions} SESSION WINDOW</>}
            />
            <RegimeStripCell
              testId="iei-hyg-strip-range"
              label="52W RANGE"
              value={
                <span style={{ display: "block", position: "relative", height: "4px", margin: "10px 0 8px", background: "var(--line-grid)" }}>
                  {rangePos != null && (
                    <span
                      data-testid="iei-hyg-range-marker"
                      data-pos={rangePos.toFixed(1)}
                      style={{
                        position: "absolute",
                        left: `calc(${rangePos.toFixed(1)}% - 1px)`,
                        top: "-5px",
                        width: "3px",
                        height: "14px",
                        background: "var(--text-primary)",
                      }}
                    />
                  )}
                </span>
              }
              sub={
                <span style={{ display: "flex", justifyContent: "space-between", gap: "8px" }}>
                  <span>
                    <span data-testid="iei-hyg-low">{formatRatio(current.ratio_52w_low)}</span>{" "}
                    {formatShortDate(current.low_date)}
                  </span>
                  <span>
                    <span data-testid="iei-hyg-high">{formatRatio(current.ratio_52w_high)}</span>{" "}
                    {formatShortDate(current.high_date)}
                  </span>
                </span>
              }
            />
            <RegimeStripCell
              testId="iei-hyg-strip-dxy"
              label="DXY"
              value={<span data-testid="iei-hyg-dxy">{formatDxy(current.dxy_close)}</span>}
              sub={<span data-testid="iei-hyg-source">{sourceText}</span>}
            />
          </RegimeStrip>
          </div>
        )}

        <FreshnessRail
          schedule={IEI_HYG_REFRESH}
          asOf={current.date}
          testId="iei-hyg-freshness-rail"
        />
      </div>

      <div className="breadth-history-block" data-testid="iei-hyg-chart-section">
        <HistoryRangeChips
          active={activeRange}
          onChange={(next) => {
            setCustomRange(null);
            setPreset(next);
          }}
          maxSessions={total}
          ariaLabel="IEI/HYG ratio chart range"
          dataTestId="iei-hyg-range-chips"
        />
        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="3-7Y TREASURIES VS HIGH YIELD (IEI / HYG RATIO)"
          xTickFormat={formatDateTick}
        />
        {total >= 2 && (
          <BrushMinimap
            values={series.map((p) => finiteOrNull(p.ratio) ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="iei-hyg-brush"
            ariaLabel="IEI/HYG ratio history range brush"
          />
        )}
      </div>
    </>
  );
}
