"use client";

import { useMemo, useState } from "react";
import { Sigma } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import {
  defaultPresetForLength,
  presetRange,
  type RangePresetSlug,
} from "@/lib/historyRange";
import {
  buildSkew2dChartRows,
  formatSkew2dChange,
  formatSkew2dRatio,
  formatZ,
  skew2dChangeColor,
  zScore,
  type Skew2dChartView,
} from "@/lib/skew2d";
import { useSkew2d } from "@/lib/useSkew2d";
import { useViewport } from "@/lib/useViewport";
import { MarketState } from "@/lib/useMarketHours";

const SKEW2D_TOOLTIP =
  "Two-session change in the SPX 25-delta put/call IV ratio at a constant 30-day maturity. Derived from the SKEW history table (Unusual Whales greeks). Sharp multi-day drops mean put skew collapsing or call demand spiking across sessions; beyond 2 sigma is a tail repricing event.";

const SOURCE_FOOTNOTE =
  "Derived from SKEW history (Unusual Whales SPX greeks). Change is ratio_t minus ratio two trading sessions prior. Stats span all sessions with a computable 2d change, not the visible range.";

const VIEWS: ReadonlyArray<{ slug: Skew2dChartView; label: string }> = [
  { slug: "change", label: "CHANGE" },
  { slug: "level", label: "LEVEL" },
];

interface Skew2dPanelChartRow {
  date: string;
  value: number | null;
  /** Never populated — CriHistoryChart wants a two-series tuple; the left slot stays inert. */
  spacer?: number | null;
}

function changeTone(color: string): "warn" | "mut" {
  return color === "var(--warning)" ? "warn" : "mut";
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

export function Skew2dPanel({ marketState }: { marketState?: MarketState }) {
  const { data, loading, syncing, lastSync } = useSkew2d(marketState ?? null);
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [view, setView] = useState<Skew2dChartView>("change");
  const [preset, setPreset] = useState<RangePresetSlug | "custom" | null>(null);
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const series = data?.series ?? [];
  const total = series.length;
  const activePreset: RangePresetSlug | "custom" = preset ?? defaultPresetForLength(total);

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
    return <SpectralLoader label="Loading 2d skew series" />;
  }

  if (!data || data.missing || !data.current || !data.stats || series.length === 0) {
    return (
      <SectionEmptyState
        icon={Sigma}
        headline="No 2d skew data yet"
        secondary="The skew2d refresh timer populates this tab from the SKEW history table. Data appears after the first successful pull."
      />
    );
  }

  const current = data.current;
  const stats = data.stats;
  const changeColor = skew2dChangeColor(current.change, stats.stddev);
  const z = zScore(current.change, stats.stddev);

  const rows: Skew2dPanelChartRow[] = buildSkew2dChartRows(series, view);
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const activeView = VIEWS.find((v) => v.slug === view) ?? VIEWS[0];
  const chartSeries: [ChartSeries<Skew2dPanelChartRow>, ChartSeries<Skew2dPanelChartRow>] = [
    {
      key: "spacer",
      label: "",
      color: "transparent",
      axis: "left",
      format: () => "",
    },
    {
      key: "value",
      label: `2D ${activeView.label}`,
      color: chartSeriesColor("comparison"),
      axis: "right",
      scaleType: "linear",
      format: view === "change" ? formatSkew2dChange : formatSkew2dRatio,
    },
  ];

  return (
    <>
      {/* ── Summary strip ─────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Sigma size={14} />
            2d Change in Put/Call Skew
            <InfoTooltip text={SKEW2D_TOOLTIP} />
          </div>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-meta)", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="skew2d-mobile-grid">
            <MetricCell
              label="CHANGE"
              value={formatSkew2dChange(current.change)}
              tone={changeTone(changeColor)}
            />
            <MetricCell label="LEVEL" value={formatSkew2dRatio(current.ratio)} />
            <MetricCell label="Z-SCORE" value={formatZ(z)} />
            <MetricCell label="HIGH" value={formatSkew2dChange(stats.high)} />
            <MetricCell label="LOW" value={formatSkew2dChange(stats.low)} />
            <MetricCell label="STDDEV" value={formatSkew2dRatio(stats.stddev)} />
            <MetricCell label="LATEST DATE" value={current.date} />
            <MetricCell label="TENOR" value="30D CM" />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="skew2d-strip-change"
              label="CHANGE"
              value={
                <span data-testid="skew2d-change-value" style={{ color: changeColor }}>
                  {formatSkew2dChange(current.change)}
                </span>
              }
              sub={<>2 SESSION CHANGE · BEYOND 2 SIGMA IS A TAIL EVENT</>}
            />
            <RegimeStripCell
              testId="skew2d-strip-level"
              label="LEVEL"
              value={formatSkew2dRatio(current.ratio)}
              sub={<>25D PUT IV OVER CALL IV</>}
            />
            <RegimeStripCell
              testId="skew2d-strip-z"
              label="Z-SCORE"
              value={formatZ(z)}
              sub={<>CHANGE OVER STDDEV</>}
            />
            <RegimeStripCell
              testId="skew2d-strip-high"
              label="HIGH"
              value={formatSkew2dChange(stats.high)}
              sub={<>LARGEST 2D RISE</>}
            />
            <RegimeStripCell
              testId="skew2d-strip-low"
              label="LOW"
              value={formatSkew2dChange(stats.low)}
              sub={<>LARGEST 2D DROP</>}
            />
            <RegimeStripCell
              testId="skew2d-strip-stddev"
              label="STDDEV"
              value={formatSkew2dRatio(stats.stddev)}
              sub={<>POPULATION, ALL NON NULL 2D CHANGES</>}
            />
            <RegimeStripCell
              testId="skew2d-strip-date"
              label="LATEST DATE"
              value={current.date}
              sub={<>FINAL SESSION</>}
            />
            <RegimeStripCell
              testId="skew2d-strip-tenor"
              label="TENOR"
              value="30D CM"
              sub={
                <>
                  {current.expiry} ({current.dte}D)
                </>
              }
            />
          </RegimeStrip>
        )}
      </div>

      {/* ── Skew2d change/level chart ─────────────────────── */}
      <div className="breadth-history-block" data-testid="skew2d-chart-section">
        <HistoryRangeChips
          active={activePreset}
          onChange={(slug) => {
            setCustomRange(null);
            setPreset(slug);
          }}
          maxSessions={total}
          ariaLabel="Skew 2d chart range"
          dataTestId="skew2d-range-chips"
        />

        <nav className="history-range-chips" aria-label="Skew 2d series view" data-testid="skew2d-view-chips">
          {VIEWS.map((entry) => (
            <button
              key={entry.slug}
              type="button"
              className={`history-range-chip${view === entry.slug ? " is-active" : ""}`}
              aria-pressed={view === entry.slug}
              onClick={() => setView(entry.slug)}
            >
              {entry.label}
            </button>
          ))}
        </nav>

        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="2D CHANGE IN 1M SPX PUT/CALL SKEW"
          xTickFormat={formatDayTick}
        />
        {total >= 2 && (
          <BrushMinimap
            values={series.map((e) => e.ratio)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="skew2d-brush"
            ariaLabel="Skew 2d history range brush"
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

export default Skew2dPanel;
