"use client";

import { useMemo, useState } from "react";
import { TrendingUp } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import {
  MARGIN_RANGE_PRESETS,
  buildMarginChartRows,
  formatLevelBn,
  formatMonthTick,
  formatSourceDate,
  formatYoyPct,
  marginPresetRange,
  marginRightViews,
  marginViewSpec,
  yoyColor,
  type MarginChartRow,
  type MarginRangeSlug,
  type MarginRightView,
} from "@/lib/marginDebt";
import { useMarginDebt } from "@/lib/useMarginDebt";
import { useViewport } from "@/lib/useViewport";

const SPLICE_FOOTNOTE =
  "FINRA debit balances Jan 1997+; NYSE member-firm series 1959-1996 ratio-adjusted ×1.039 at the splice. YoY is null across the 1997 seam.";

function yoyTone(v: number | null | undefined): "pos" | "neg" | "warn" | "mut" {
  const color = yoyColor(v);
  if (color === "var(--warning)") return "warn";
  if (color === "var(--positive)") return "pos";
  if (color === "var(--negative)") return "neg";
  return "mut";
}

export default function MarginDebtPanel() {
  const { data, loading, syncing, lastSync } = useMarginDebt();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [rightView, setRightView] = useState<MarginRightView>("yoy");
  const [smooth, setSmooth] = useState(false);
  // 65-year monthly chart — default to the full series, unlike the
  // session-based regime charts that default to a recent window.
  const [activeRange, setActiveRange] = useState<MarginRangeSlug | "custom">("all");
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
    return marginPresetRange(activeRange === "custom" ? "all" : activeRange, total);
  }, [activeRange, customRange, total]);

  if ((loading || syncing) && !data) {
    return <SpectralLoader label="Loading FINRA margin debt series" />;
  }

  if (!data || data.missing || !data.current || series.length === 0) {
    return (
      <SectionEmptyState
        icon={TrendingUp}
        headline="No margin debt data yet"
        secondary="The margin-debt refresh timer populates this tab from FINRA's monthly debit-balance series. Data appears after the first successful pull."
      />
    );
  }

  const current = data.current;
  const normalizationAvailable = data.normalization?.available === true;
  const views = marginRightViews(normalizationAvailable);
  // Normalization can drop out between payloads; never strand a hidden view.
  const spec = views.some((v) => v.slug === rightView)
    ? marginViewSpec(rightView)
    : marginViewSpec("yoy");

  const rows = buildMarginChartRows(series, spec, smooth);
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<MarginChartRow>, ChartSeries<MarginChartRow>] = [
    {
      key: "spx",
      label: "S&P 500",
      color: chartSeriesColor("primary"),
      axis: "left",
      scaleType: "log",
      format: (v: number) => v.toFixed(0),
    },
    {
      key: "right",
      label: `MARGIN DEBT ${spec.label}${smooth ? " (3MO MA)" : ""}`,
      color: chartSeriesColor("fault"),
      axis: "right",
      scaleType: spec.scaleType,
      format: spec.format,
    },
  ];

  return (
    <>
      {/* ── Summary strip ─────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <TrendingUp size={14} />
            Margin Debt Acceleration
            <InfoTooltip text="FINRA member-firm margin debit balances vs the S&P 500, monthly since 1959. Year-over-year acceleration above +50% has preceded every major top (2000, 2007, 2021): extreme growth in borrowed exposure is froth, not health. YoY contraction marks forced deleveraging." />
          </div>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="margin-debt-mobile-grid">
            <MetricCell label="LEVEL" value={formatLevelBn(current.level)} />
            <MetricCell
              label="YOY 12M"
              value={formatYoyPct(current.level_yoy_pct)}
              tone={yoyTone(current.level_yoy_pct)}
            />
            <MetricCell label="MONTH" value={current.date} />
            <MetricCell label="SOURCE" value={formatSourceDate(data.source_last_modified)} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="margin-debt-strip-level"
              label="MARGIN DEBT"
              value={formatLevelBn(current.level)}
              sub={<>FINRA DEBIT BALANCES</>}
            />
            <RegimeStripCell
              testId="margin-debt-strip-yoy"
              label="YOY 12M"
              value={
                <span data-testid="margin-debt-yoy-value" style={{ color: yoyColor(current.level_yoy_pct) }}>
                  {formatYoyPct(current.level_yoy_pct)}
                </span>
              }
              sub={<>ABOVE +50% READS AS FROTH</>}
            />
            <RegimeStripCell
              testId="margin-debt-strip-month"
              label="LATEST MONTH"
              value={current.date}
              sub={<>MONTHLY SERIES SINCE 1959</>}
            />
            <RegimeStripCell
              testId="margin-debt-strip-source"
              label="SOURCE UPDATED"
              value={formatSourceDate(data.source_last_modified)}
              sub={<>FINRA MARGIN STATISTICS</>}
            />
          </RegimeStrip>
        )}
      </div>

      {/* ── S&P 500 vs margin debt chart ──────────────────── */}
      <div className="breadth-history-block" data-testid="margin-debt-chart-section">
        <nav className="history-range-chips" aria-label="Margin debt chart range" data-testid="margin-debt-range-chips">
          {MARGIN_RANGE_PRESETS.filter((p) => p.months <= total || p.slug === "all").map((preset) => (
            <button
              key={preset.slug}
              type="button"
              className={`history-range-chip${activeRange === preset.slug ? " is-active" : ""}`}
              aria-pressed={activeRange === preset.slug}
              onClick={() => {
                setCustomRange(null);
                setActiveRange(preset.slug);
              }}
            >
              {preset.label}
            </button>
          ))}
          {activeRange === "custom" ? (
            <span className="history-range-chip is-active history-range-chip-custom" aria-label="Custom range from brush selection">
              Custom
            </span>
          ) : null}
        </nav>

        <nav className="history-range-chips" aria-label="Margin debt series view" data-testid="margin-debt-view-chips">
          {views.map((view) => (
            <button
              key={view.slug}
              type="button"
              className={`history-range-chip${spec.slug === view.slug ? " is-active" : ""}`}
              aria-pressed={spec.slug === view.slug}
              onClick={() => setRightView(view.slug)}
            >
              {view.label}
            </button>
          ))}
          <button
            type="button"
            className={`history-range-chip${smooth ? " is-active" : ""}`}
            aria-pressed={smooth}
            onClick={() => setSmooth((prev) => !prev)}
            data-testid="margin-debt-smooth-toggle"
          >
            3MO MA
          </button>
        </nav>

        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title={`S&P 500 VS MARGIN DEBT ${spec.label}`}
          xTickFormat={formatMonthTick}
        />
        {total >= 2 && (
          <BrushMinimap
            values={series.map((e) => e.level ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setActiveRange("custom")}
            testIdPrefix="margin-debt-brush"
            ariaLabel="Margin debt history range brush"
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
          {SPLICE_FOOTNOTE}
        </div>
      </div>
    </>
  );
}
