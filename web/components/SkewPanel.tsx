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
import { LiveBadge, RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import {
  defaultPresetForLength,
  presetRange,
  type RangePresetSlug,
} from "@/lib/historyRange";
import {
  buildSkewChartRows,
  formatIvPct,
  formatSkewChange,
  formatSkewRatio,
  formatZ,
  isFreshIntradaySkew,
  skewChangeColor,
  zScore,
  type SkewChartView,
} from "@/lib/skew";
import { useSkew } from "@/lib/useSkew";
import { useViewport } from "@/lib/useViewport";
import { MarketState } from "@/lib/useMarketHours";

const SKEW_TOOLTIP =
  "Ratio of SPX 25-delta put IV to 25-delta call IV at a constant 30-day maturity: each wing is interpolated in delta from the Unusual Whales chain, then in time between the two bracketing monthly expiries. The chart plots the day-over-day change: sharp drops mean put skew collapsing or call demand spiking; sharp rises mean downside fear getting bid. Beyond 2 sigma is a tail repricing event.";

const SOURCE_FOOTNOTE =
  "Unusual Whales SPX greeks chains, constant-maturity 30d interpolated between the bracketing monthlies. Stats span all sessions with a computable change, not the visible range.";

const VIEWS: ReadonlyArray<{ slug: SkewChartView; label: string }> = [
  { slug: "change", label: "CHANGE" },
  { slug: "level", label: "LEVEL" },
];

interface SkewPanelChartRow {
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

export default function SkewPanel({ marketState }: { marketState?: MarketState }) {
  const { data, loading, syncing, lastSync } = useSkew(marketState ?? null);
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [view, setView] = useState<SkewChartView>("change");
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
    return <SpectralLoader label="Loading UW skew series" />;
  }

  if (!data || data.missing || !data.current || !data.stats || series.length === 0) {
    return (
      <SectionEmptyState
        icon={Sigma}
        headline="No skew data yet"
        secondary="The skew refresh timer populates this tab from the Unusual Whales SPX greeks chain. Data appears after the first successful pull."
      />
    );
  }

  const current = data.current;
  const stats = data.stats;
  const changeColor = skewChangeColor(current.change, stats.stddev);
  const z = zScore(current.change, stats.stddev);
  const live = data.market_status === "open" && isFreshIntradaySkew(current);
  const asOf = current.as_of
    ? new Date(current.as_of).toLocaleTimeString("en-US", {
        hour: "numeric",
        minute: "2-digit",
        timeZone: "America/New_York",
        timeZoneName: "short",
      })
    : null;

  const rows: SkewPanelChartRow[] = buildSkewChartRows(series, view);
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const activeView = VIEWS.find((v) => v.slug === view) ?? VIEWS[0];
  const chartSeries: [ChartSeries<SkewPanelChartRow>, ChartSeries<SkewPanelChartRow>] = [
    {
      key: "spacer",
      label: "",
      color: "transparent",
      axis: "left",
      format: () => "",
    },
    {
      key: "value",
      label: `25D PUT/CALL ${activeView.label}`,
      color: chartSeriesColor("comparison"),
      axis: "right",
      scaleType: "linear",
      format: view === "change" ? formatSkewChange : formatSkewRatio,
    },
  ];

  return (
    <>
      {/* ── Summary strip ─────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Sigma size={14} />
            25-Delta Put/Call Skew
            <InfoTooltip text={SKEW_TOOLTIP} />
            <span data-testid="skew-live-status">
              <LiveBadge live={live} />
            </span>
          </div>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-meta)", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="skew-mobile-grid">
            <MetricCell
              label="CHANGE"
              value={formatSkewChange(current.change)}
              tone={changeTone(changeColor)}
            />
            <MetricCell label="LEVEL" value={formatSkewRatio(current.ratio)} />
            <MetricCell label="Z-SCORE" value={formatZ(z)} />
            <MetricCell label="25D PUT IV" value={formatIvPct(current.put_iv)} />
            <MetricCell label="25D CALL IV" value={formatIvPct(current.call_iv)} />
            <MetricCell label="HIGH" value={formatSkewChange(stats.high)} />
            <MetricCell label="LOW" value={formatSkewChange(stats.low)} />
            <MetricCell label="STDDEV" value={formatSkewRatio(stats.stddev)} />
            <MetricCell label="LATEST DATE" value={current.date} />
            <MetricCell label="TENOR" value="30D CM" />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="skew-strip-change"
              label="CHANGE"
              value={
                <span data-testid="skew-change-value" style={{ color: changeColor }}>
                  {formatSkewChange(current.change)}
                </span>
              }
              sub={<>BEYOND 2 SIGMA IS A TAIL EVENT</>}
            />
            <RegimeStripCell
              testId="skew-strip-level"
              label="LEVEL"
              value={formatSkewRatio(current.ratio)}
              sub={<>25D PUT IV OVER CALL IV</>}
            />
            <RegimeStripCell
              testId="skew-strip-z"
              label="Z-SCORE"
              value={formatZ(z)}
              sub={<>CHANGE OVER STDDEV</>}
            />
            <RegimeStripCell
              testId="skew-strip-put-iv"
              label="25D PUT IV"
              value={formatIvPct(current.put_iv)}
              sub={<>INTERPOLATED AT -0.25 DELTA</>}
            />
            <RegimeStripCell
              testId="skew-strip-call-iv"
              label="25D CALL IV"
              value={formatIvPct(current.call_iv)}
              sub={<>INTERPOLATED AT +0.25 DELTA</>}
            />
            <RegimeStripCell
              testId="skew-strip-high"
              label="HIGH"
              value={formatSkewChange(stats.high)}
              sub={<>LARGEST DAILY RISE</>}
            />
            <RegimeStripCell
              testId="skew-strip-low"
              label="LOW"
              value={formatSkewChange(stats.low)}
              sub={<>LARGEST DAILY DROP</>}
            />
            <RegimeStripCell
              testId="skew-strip-stddev"
              label="STDDEV"
              value={formatSkewRatio(stats.stddev)}
              sub={<>POPULATION, ALL NON NULL CHANGES</>}
            />
            <RegimeStripCell
              testId="skew-strip-date"
              label="LATEST DATE"
              value={current.date}
              sub={current.is_intraday ? <>INTRADAY{asOf ? ` · AS OF ${asOf}` : ""}</> : <>FINAL SESSION</>}
            />
            <RegimeStripCell
              testId="skew-strip-tenor"
              label="TENOR"
              value="30D CM"
              sub={
                <>
                  {current.expiry} ({current.dte}D) X {current.expiry_far ?? current.expiry} (
                  {current.dte_far ?? current.dte}D)
                </>
              }
            />
          </RegimeStrip>
        )}
      </div>

      {/* ── Skew change/level chart ───────────────────────── */}
      <div className="breadth-history-block" data-testid="skew-chart-section">
        <HistoryRangeChips
          active={activePreset}
          onChange={(slug) => {
            setCustomRange(null);
            setPreset(slug);
          }}
          maxSessions={total}
          ariaLabel="Skew chart range"
          dataTestId="skew-range-chips"
        />

        <nav className="history-range-chips" aria-label="Skew series view" data-testid="skew-view-chips">
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
          title="SPX 1M 25D PUT/CALL SKEW"
          xTickFormat={formatDayTick}
        />
        {total >= 2 && (
          <BrushMinimap
            values={series.map((e) => e.ratio)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="skew-brush"
            ariaLabel="Skew history range brush"
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
