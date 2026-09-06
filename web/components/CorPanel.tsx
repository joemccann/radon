"use client";

import { useMemo, useState } from "react";
import { Network } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import { presetRange, type RangePresetSlug } from "@/lib/historyRange";
import {
  buildCorChartRows,
  corRegime,
  corRegimeColor,
  formatCor,
  formatCorChange,
  formatPercentile,
  latestSourceStamp,
  termSpread,
  TENOR_OPTIONS,
  type CorChartRow,
  type CorTenorKey,
} from "@/lib/cor";
import { formatSourceDate } from "@/lib/straddle";
import { useCor } from "@/lib/useCor";
import { useViewport } from "@/lib/useViewport";

const COR_TOOLTIP =
  "Market-implied average pairwise correlation of the top-50 SPX constituents, backed out from index option prices versus single-stock option prices, per tenor. The regime reads the 6M tenor's all-history percentile: below the 10th percentile is COMPRESSED (dispersion positioning crowded, index hedges cheap, and correlation snaps toward 1 in systemic selloffs), above the 90th is STRESS already in the price, anything between is NEUTRAL.";

const SOURCE_FOOTNOTE =
  "Cboe COR1M, COR3M, COR6M and COR1Y daily closes since 2006-01. Stats span each tenor's non-null sessions, not the visible range.";

interface CorPanelChartRow extends CorChartRow {
  /** Never populated: CriHistoryChart wants a two-series tuple; the left slot stays inert. */
  spacer?: number | null;
}

/** Daily-series x-tick over a multi-decade span: "05 Aug 26". */
function formatDayTick(d: Date): string {
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "2-digit",
    timeZone: "UTC",
  });
}

export default function CorPanel() {
  const { data, loading, syncing, lastSync } = useCor();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [tenor, setTenor] = useState<CorTenorKey>("cor6m");
  const [preset, setPreset] = useState<RangePresetSlug | "custom" | null>(null);
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const series = data?.series ?? [];
  const total = series.length;
  // Multi-decade series: default to the full history, not a recent slice.
  const activePreset: RangePresetSlug | "custom" = preset ?? "all";

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
    return <SpectralLoader label="Loading Cboe implied correlation series" />;
  }

  if (!data || data.missing || !data.current || !data.stats || series.length === 0) {
    return (
      <SectionEmptyState
        icon={Network}
        headline="No implied correlation data yet"
        secondary="The radon-cor refresh timer populates this tab from Cboe's COR1M, COR3M, COR6M and COR1Y daily series. Data appears after the first successful pull."
      />
    );
  }

  const current = data.current;
  const spread = termSpread(current);
  const percentile = data.stats.cor6m?.percentile ?? null;
  const regime = corRegime(percentile);
  const regimeTone = corRegimeColor(regime);
  const sourceStamp = latestSourceStamp(data.source_last_modified);
  const activeTenor = TENOR_OPTIONS.find((o) => o.key === tenor) ?? TENOR_OPTIONS[2];

  const rows: CorPanelChartRow[] = buildCorChartRows(series, tenor);
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<CorPanelChartRow>, ChartSeries<CorPanelChartRow>] = [
    {
      key: "spacer",
      label: "",
      color: "transparent",
      axis: "left",
      format: () => "",
    },
    {
      key: "value",
      label: `COR ${activeTenor.label}`,
      color: chartSeriesColor("comparison"),
      axis: "right",
      scaleType: "linear",
      format: formatCor,
    },
  ];

  return (
    <>
      {/* ── Summary strip ─────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Network size={14} />
            SPX Implied Correlation
            <InfoTooltip text={COR_TOOLTIP} />
          </div>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-meta)", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="cor-mobile-grid">
            <MetricCell label="COR 1M" value={formatCor(current.cor1m)} />
            <MetricCell label="COR 3M" value={formatCor(current.cor3m)} />
            <MetricCell label="COR 6M" value={formatCor(current.cor6m)} />
            <MetricCell label="COR 1Y" value={formatCor(current.cor1y)} />
            <MetricCell label="1Y-1M SPREAD" value={formatCorChange(spread)} />
            <MetricCell label="6M PCTILE" value={`${formatPercentile(percentile)} ${regime}`} />
            <MetricCell label="DATE" value={current.date} />
            <MetricCell label="SOURCE" value={formatSourceDate(sourceStamp)} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="cor-strip-1m"
              label="COR 1M"
              value={formatCor(current.cor1m)}
              sub={<>1D {formatCorChange(current.change_1d.cor1m)}</>}
            />
            <RegimeStripCell
              testId="cor-strip-3m"
              label="COR 3M"
              value={formatCor(current.cor3m)}
              sub={<>1D {formatCorChange(current.change_1d.cor3m)}</>}
            />
            <RegimeStripCell
              testId="cor-strip-6m"
              label="COR 6M"
              value={formatCor(current.cor6m)}
              sub={<>1D {formatCorChange(current.change_1d.cor6m)}</>}
            />
            <RegimeStripCell
              testId="cor-strip-1y"
              label="COR 1Y"
              value={formatCor(current.cor1y)}
              sub={<>1D {formatCorChange(current.change_1d.cor1y)}</>}
            />
            <RegimeStripCell
              testId="cor-strip-spread"
              label="1Y-1M SPREAD"
              value={formatCorChange(spread)}
              sub={<>POSITIVE = UPWARD SLOPING</>}
            />
            <RegimeStripCell
              testId="cor-strip-regime"
              label="6M PCTILE"
              value={
                <span data-testid="cor-regime-value" style={{ color: regimeTone }}>
                  <span>{formatPercentile(percentile)}</span> {regime}
                </span>
              }
              sub={<>SHARE OF HISTORY BELOW LATEST</>}
            />
            <RegimeStripCell
              testId="cor-strip-date"
              label="LATEST DATE"
              value={current.date}
              sub={<>DAILY SERIES SINCE 2006-01</>}
            />
            <RegimeStripCell
              testId="cor-strip-source"
              label="SOURCE UPDATED"
              value={formatSourceDate(sourceStamp)}
              sub={<>NEWEST CBOE HISTORY FILE</>}
            />
          </RegimeStrip>
        )}
      </div>

      {/* ── Implied correlation chart ─────────────────────── */}
      <div className="breadth-history-block" data-testid="cor-chart-section">
        <HistoryRangeChips
          active={activePreset}
          onChange={(slug) => {
            setCustomRange(null);
            setPreset(slug);
          }}
          maxSessions={total}
          ariaLabel="Implied correlation chart range"
          dataTestId="cor-range-chips"
        />

        <nav className="history-range-chips" aria-label="Implied correlation tenor" data-testid="cor-tenor-chips">
          {TENOR_OPTIONS.map((option) => (
            <button
              key={option.key}
              type="button"
              className={`history-range-chip${tenor === option.key ? " is-active" : ""}`}
              aria-pressed={tenor === option.key}
              onClick={() => setTenor(option.key)}
            >
              {option.label}
            </button>
          ))}
        </nav>

        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="SPX IMPLIED CORRELATION"
          xTickFormat={formatDayTick}
        />
        {total >= 2 && (
          <BrushMinimap
            values={series.map((e) => e[tenor])}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="cor-brush"
            ariaLabel="Implied correlation history range brush"
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
