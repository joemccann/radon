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
import { CREDIT_VIX_REFRESH } from "@/lib/refreshSchedule";
import {
  formatGap,
  formatSpread,
  formatVix,
  stateLabel,
  stateTone,
  type CreditVixState,
} from "@/lib/creditVix";
import { useCreditVix } from "@/lib/useCreditVix";
import { useViewport } from "@/lib/useViewport";

const INFO_TOOLTIP =
  "SHY minus HYG price gap, a credit proxy. Not an OAS and not quoted in basis points. Each leg is placed in its own trailing 252-session range; the gap is credit range position minus VIX range position. CREDIT WIDE means the SHY-HYG gap is elevated relative to VIX. Unadjusted closes: HYG and SHY go ex around the first business day of each month, so the spread jumps about 0.15 on those dates.";

interface CreditVixChartRow {
  date: string;
  spread: number | null;
  vix: number | null;
}

const TONE_COLOR = {
  positive: "var(--positive)",
  negative: "var(--negative)",
  muted: "var(--text-muted)",
} as const;

const TONE_MOBILE = { positive: "pos", negative: "neg", muted: "mut" } as const;

function stateColor(state: CreditVixState | null | undefined): string {
  return TONE_COLOR[stateTone(state)];
}

function finiteOrNull(v: number | null | undefined): number | null {
  return v != null && Number.isFinite(v) ? v : null;
}

export default function CreditVixPanel() {
  const { data, loading, syncing, lastSync, error } = useCreditVix();
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
    return <SpectralLoader label="Loading SHY minus HYG vs VIX series" />;
  }

  if (!data || data.missing || !data.current || series.length === 0) {
    return (
      <SectionEmptyState
        icon={Scale}
        headline="No SHY minus HYG vs VIX snapshot"
        secondary="Waiting for the credit-vix refresh timer"
      />
    );
  }

  const current = data.current;
  const rows: CreditVixChartRow[] = series.map((p) => ({
    date: p.date,
    spread: finiteOrNull(p.spread),
    vix: finiteOrNull(p.vix_close),
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<CreditVixChartRow>, ChartSeries<CreditVixChartRow>] = [
    {
      key: "vix",
      label: "VIX",
      color: chartSeriesColor("fault"),
      axis: "left",
      scaleType: "linear",
      format: (v: number) => v.toFixed(2),
    },
    {
      key: "spread",
      label: "SHY - HYG",
      color: chartSeriesColor("primary"),
      axis: "right",
      scaleType: "linear",
      format: (v: number) => v.toFixed(2),
    },
  ];

  const sourceText = `${(data.source ?? "---").toUpperCase()} ${formatSessionDate(current.date)}`;
  const widest = current.widest_since ? formatSessionDate(current.widest_since) : "none";

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Scale size={14} />
            SHY MINUS HYG VS VIX
            <InfoTooltip text={INFO_TOOLTIP} />
          </div>
          <PanelRefreshError error={error} testId="credit-vix-refresh-error" />
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-meta)", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="credit-vix-mobile-grid">
            <MetricCell label="SPREAD" value={formatSpread(current.spread)} />
            <MetricCell
              label="STATE"
              value={stateLabel(current.state)}
              tone={TONE_MOBILE[stateTone(current.state)]}
            />
            <MetricCell label="VIX" value={formatVix(current.vix_close)} />
            <MetricCell label="GAP" value={formatGap(current.gap)} />
            <MetricCell label="WIDEST SINCE" value={widest} />
            <MetricCell label="SOURCE" value={sourceText} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="credit-vix-strip-spread"
              label="SPREAD"
              value={<span data-testid="credit-vix-spread">{formatSpread(current.spread)}</span>}
              sub={<>SHY MINUS HYG PRICE GAP</>}
            />
            <RegimeStripCell
              testId="credit-vix-strip-vix"
              label="VIX"
              value={<span data-testid="credit-vix-vix">{formatVix(current.vix_close)}</span>}
              sub={<span data-testid="credit-vix-source">{sourceText}</span>}
            />
            <RegimeStripCell
              testId="credit-vix-strip-gap"
              label="GAP"
              value={<span data-testid="credit-vix-gap">{formatGap(current.gap)}</span>}
              sub={<>{current.window_sessions} SESSION WINDOW</>}
            />
            <RegimeStripCell
              testId="credit-vix-strip-state"
              label="STATE"
              value={
                <span data-testid="credit-vix-state" style={{ color: stateColor(current.state) }}>
                  {stateLabel(current.state)}
                </span>
              }
              sub={<>WIDEST SINCE <span data-testid="credit-vix-widest-since">{widest}</span></>}
            />
          </RegimeStrip>
        )}

        <FreshnessRail
          schedule={CREDIT_VIX_REFRESH}
          asOf={current.date}
          testId="credit-vix-freshness-rail"
        />
      </div>

      <div className="breadth-history-block" data-testid="credit-vix-chart-section">
        <HistoryRangeChips
          active={activeRange}
          onChange={(next) => {
            setCustomRange(null);
            setPreset(next);
          }}
          maxSessions={total}
          ariaLabel="SHY minus HYG vs VIX chart range"
          dataTestId="credit-vix-range-chips"
        />
        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="SHY MINUS HYG VS VIX"
          xTickFormat={formatDateTick}
        />
        {total >= 2 && (
          <BrushMinimap
            values={series.map((p) => finiteOrNull(p.spread) ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="credit-vix-brush"
            ariaLabel="SHY minus HYG vs VIX history range brush"
          />
        )}
      </div>
    </>
  );
}
