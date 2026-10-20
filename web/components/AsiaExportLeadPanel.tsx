"use client";

import { useMemo, useState } from "react";
import { TrendingUp } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import FreshnessRail from "./FreshnessRail";
import InfoTooltip from "./InfoTooltip";
import RequestError from "./RequestError";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import type { AsiaExportLeadEntry } from "@/lib/asiaExportLead";
import { chartSeriesColor } from "@/lib/chartSystem";
import { formatMonthTick } from "@/lib/marginDebt";
import { ASIA_EXPORT_LEAD_REFRESH } from "@/lib/refreshSchedule";
import { useAsiaExportLead } from "@/lib/useAsiaExportLead";
import { useViewport } from "@/lib/useViewport";

const PRESETS = [
  { slug: "1y", label: "1Y", months: 12 },
  { slug: "3y", label: "3Y", months: 36 },
  { slug: "5y", label: "5Y", months: 60 },
  { slug: "all", label: "All", months: Infinity },
] as const;
type Range = (typeof PRESETS)[number]["slug"] | "custom";
type ChartRow = {
  date: string;
  sourceMonth: string;
  composite: number | null;
  taiwan: number | null;
  korea: number | null;
};
const MONTH = /^(\d{4})-(0[1-9]|1[0-2])$/;
const finite = (value: number | null | undefined): number | null =>
  value != null && Number.isFinite(value) ? value : null;
const signed = (value: number | null | undefined, suffix = "%") =>
  finite(value) == null
    ? "---"
    : `${value! >= 0 ? "+" : ""}${value!.toFixed(1)}${suffix}`;
const valueColor = (value: number | null | undefined) =>
  finite(value) == null || value === 0
    ? "var(--text-primary)"
    : value! > 0
      ? "var(--positive)"
      : "var(--negative)";

function monthIndex(month: string): number | null {
  const match = MONTH.exec(month);
  return match ? Number(match[1]) * 12 + Number(match[2]) - 1 : null;
}
function monthKey(index: number): string {
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;
}
/** Materialize absent calendar months so neither the line nor the brush bridges them. */
export function exportChartRows(
  series: readonly AsiaExportLeadEntry[],
  shift: boolean,
): ChartRow[] {
  const months = new Map<number, AsiaExportLeadEntry>();
  for (const row of series) {
    const index = monthIndex(row.date);
    if (index != null) months.set(index, row);
  }
  const keys = [...months.keys()].sort((a, b) => a - b);
  if (!keys.length) return [];
  const rows: ChartRow[] = [];
  for (let index = keys[0]; index <= keys[keys.length - 1]; index++) {
    const row = months.get(index);
    rows.push({
      date: monthKey(index + (shift ? 6 : 0)),
      sourceMonth: monthKey(index),
      composite: finite(row?.ma3_yoy_pct),
      taiwan: finite(row?.taiwan_yoy_pct),
      korea: finite(row?.korea_yoy_pct),
    });
  }
  return rows;
}
function sampleClock(value: string | null): string {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime())
    ? `${date.toISOString().slice(0, 19).replace("T", " ")} UTC`
    : "---";
}

const TOOLTIP =
  "Each country's total exports are measured in USD. The composite equally weights both year-over-year growth rates for the same month, then averages three consecutive calendar months. A missing country or month leaves a gap. Shift +6M moves already observed export dates six calendar months for comparison only; the proposed EPS relationship has not been validated.";

export default function AsiaExportLeadPanel() {
  const { data, loading, syncing, error, syncNow } = useAsiaExportLead();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;
  const [view, setView] = useState<"composite" | "countries">("composite");
  const [shift, setShift] = useState(false);
  const [range, setRange] = useState<Range>("all");
  const [custom, setCustom] = useState<[number, number] | null>(null);
  const sourceSeries = data?.series;
  const rows = useMemo(
    () => exportChartRows(sourceSeries ?? [], view === "composite" && shift),
    [sourceSeries, view, shift],
  );
  const last = Math.max(0, rows.length - 1);
  const chartRange: [number, number] = (() => {
    if (range === "custom" && custom)
      return [
        Math.max(0, Math.min(custom[0], last)),
        Math.max(0, Math.min(custom[1], last)),
      ];
    const months =
      PRESETS.find((preset) => preset.slug === range)?.months ?? Infinity;
    return [
      Number.isFinite(months) ? Math.max(0, rows.length - months) : 0,
      last,
    ];
  })();

  if ((loading || syncing) && !data)
    return (
      <SpectralLoader label="Loading Taiwan and South Korea export data" />
    );
  if (!data || data.missing || !data.current || rows.length === 0) {
    return (
      <>
        <RequestError error={error} onRetry={syncNow} />
        <SectionEmptyState
          icon={TrendingUp}
          headline="No export data yet"
          secondary="Awaiting a successful sample of Taiwan MOF and South Korea KCS monthly total exports."
        />
      </>
    );
  }

  const current = data.current;
  const growth =
    finite(current.ma3_yoy_pct) == null
      ? "unavailable"
      : current.ma3_yoy_pct! > 0
        ? "expanding"
        : current.ma3_yoy_pct! < 0
          ? "contracting"
          : "flat";
  const momentum =
    finite(current.ma3_change_pp) == null
      ? "unavailable"
      : current.ma3_change_pp! > 0
        ? "accelerating"
        : current.ma3_change_pp! < 0
          ? "slowing"
          : "unchanged";
  const sourceMonth = current.date;
  const sampled = sampleClock(data.scan_time);
  const chartSeries: [ChartSeries<ChartRow>, ChartSeries<ChartRow>?] =
    view === "countries"
      ? [
          {
            key: "taiwan",
            label: "Taiwan YoY",
            color: chartSeriesColor("primary"),
            axis: "left",
            scaleType: "linear",
            format: signed,
          },
          {
            key: "korea",
            label: "South Korea YoY",
            color: chartSeriesColor("comparison"),
            axis: "right",
            scaleType: "linear",
            format: signed,
          },
        ]
      : [
          {
            key: "composite",
            label: "Equal-weight export YoY, 3-month mean",
            color: chartSeriesColor("primary"),
            axis: "left",
            scaleType: "linear",
            format: signed,
          },
        ];
  const metrics = [
    {
      key: "composite",
      label: "COMPOSITE MA3",
      value: signed(current.ma3_yoy_pct),
      numeric: current.ma3_yoy_pct,
      sub: `${sourceMonth} growth: ${growth}`,
    },
    {
      key: "taiwan",
      label: "TAIWAN YOY",
      value: signed(current.taiwan_yoy_pct),
      numeric: current.taiwan_yoy_pct,
      sub: `${sourceMonth} total exports`,
    },
    {
      key: "korea",
      label: "SOUTH KOREA YOY",
      value: signed(current.korea_yoy_pct),
      numeric: current.korea_yoy_pct,
      sub: `${sourceMonth} total exports`,
    },
    {
      key: "change",
      label: "MA3 CHANGE",
      value: signed(current.ma3_change_pp, " pp"),
      numeric: current.ma3_change_pp,
      sub: `${sourceMonth} momentum: ${momentum}`,
    },
  ];
  return (
    <>
      <RequestError error={error} onRetry={syncNow} retainedData />
      <div className="section" data-testid="asia-export-lead-summary">
        <div className="section-header">
          <div className="section-title">
            <TrendingUp size={14} />
            Taiwan and South Korea export lead
            <InfoTooltip text={TOOLTIP} />
          </div>
        </div>
        {compact ? (
          <div
            className="m-regime-grid2x2"
            data-testid="asia-export-lead-mobile-grid"
          >
            {metrics.map((metric) => (
              <div key={metric.key} style={{ minWidth: 0 }}>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  {metric.label}
                </span>
                <strong
                  style={{
                    fontSize: 24,
                    fontVariantNumeric: "tabular-nums",
                    color: valueColor(metric.numeric),
                  }}
                >
                  {metric.value}
                </strong>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  {metric.sub}
                </span>
              </div>
            ))}
            <div>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                SOURCE MONTH
              </span>
              <strong style={{ fontSize: 18 }}>{sourceMonth}</strong>
            </div>
            <div>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                SAMPLED UTC
              </span>
              <strong style={{ fontSize: 18 }}>
                {sampled === "---" ? sampled : sampled.slice(11, 19)}
              </strong>
            </div>
          </div>
        ) : (
          <>
            <RegimeStrip columns={4} className="regime-strip--fit">
              {metrics.map((metric) => (
                <RegimeStripCell
                  key={metric.key}
                  testId={`asia-export-lead-strip-${metric.key}`}
                  label={metric.label}
                  value={metric.value}
                  sub={metric.sub}
                />
              ))}
            </RegimeStrip>
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: "8px 24px",
                padding: "12px 22px",
                fontSize: 12,
                color: "var(--text-muted)",
              }}
            >
              <span>
                Source month:{" "}
                <span data-testid="asia-export-lead-source-month">
                  {sourceMonth}
                </span>
              </span>
              <span>
                Sampled:{" "}
                <span data-testid="asia-export-lead-sampled">{sampled}</span>
              </span>
            </div>
          </>
        )}
        <FreshnessRail
          schedule={ASIA_EXPORT_LEAD_REFRESH}
          model="release"
          asOf={data.data_date}
          testId="asia-export-lead-freshness-rail"
          asOfTestId="asia-export-lead-strip-asof"
        />
      </div>
      <div
        className="breadth-history-block"
        data-testid="asia-export-lead-chart-section"
        data-view={view}
        data-alignment={view === "composite" && shift ? "shifted" : "observed"}
        data-range-start={rows[chartRange[0]]?.sourceMonth}
        data-range-end={rows[chartRange[1]]?.sourceMonth}
      >
        <nav className="history-range-chips" aria-label="Export history range">
          {PRESETS.map((preset) => (
            <button
              key={preset.slug}
              type="button"
              className={`history-range-chip${range === preset.slug ? " is-active" : ""}`}
              style={{ minHeight: 44, fontSize: 12 }}
              aria-pressed={range === preset.slug}
              onClick={() => {
                setCustom(null);
                setRange(preset.slug);
              }}
            >
              {preset.label}
            </button>
          ))}
          {range === "custom" ? (
            <span className="history-range-chip is-active">Custom</span>
          ) : null}
        </nav>
        <nav className="history-range-chips" aria-label="Export series view">
          {(["composite", "countries"] as const).map((item) => (
            <button
              key={item}
              type="button"
              className={`history-range-chip${view === item ? " is-active" : ""}`}
              style={{ minHeight: 44, fontSize: 12 }}
              aria-pressed={view === item}
              onClick={() => {
                setView(item);
                if (item === "countries") setShift(false);
              }}
            >
              {item === "composite" ? "Composite" : "Countries"}
            </button>
          ))}
        </nav>
        <nav
          className="history-range-chips"
          aria-label="Export calendar alignment"
        >
          {[false, true].map((item) => (
            <button
              key={String(item)}
              type="button"
              disabled={view === "countries" && item}
              className={`history-range-chip${shift === item ? " is-active" : ""}`}
              style={{ minHeight: 44, fontSize: 12 }}
              aria-pressed={shift === item}
              onClick={() => setShift(item)}
            >
              {item ? "Shift +6M" : "Observed"}
            </button>
          ))}
        </nav>
        <CriHistoryChart
          history={rows.slice(chartRange[0], chartRange[1] + 1)}
          series={chartSeries}
          title={
            view === "countries"
              ? "TOTAL EXPORTS: COUNTRY YOY"
              : `TOTAL EXPORTS: COMPOSITE MA3${shift ? " / SHIFT +6M" : " / OBSERVED"}`
          }
          sharedAxis
          referenceLevels={[{ value: 0, label: "Zero growth" }]}
          xTickFormat={formatMonthTick}
          xTickMinSpacing={90}
          maxGapMs={32 * 86400000}
        />
        <BrushMinimap
          values={rows.map((row) =>
            view === "countries" ? row.taiwan : row.composite,
          )}
          range={chartRange}
          onRangeChange={setCustom}
          onCustom={() => setRange("custom")}
          testIdPrefix="asia-export-lead-brush"
          ariaLabel="Export history range brush"
          formatIndex={(index) => rows[index]?.sourceMonth ?? "---"}
        />
        <div className="section-footnote" style={{ fontSize: 12 }}>
          <p>
            Total exports, not semiconductor-only exports. Latest-revised
            history.
          </p>
          <p>
            Six-month alignment is not an earnings forecast. MSCI US forward EPS
            comparison unavailable.
          </p>
          <p>
            Sources:{" "}
            <a
              href="https://data.gov.tw/en/datasets/8378"
              target="_blank"
              rel="noreferrer"
            >
              Taiwan MOF dataset 8378
            </a>{" "}
            and{" "}
            <a
              href="https://www.tradedata.go.kr/cts/index_eng.do"
              target="_blank"
              rel="noreferrer"
            >
              South Korea KCS
            </a>
            . Source updated: {sourceMonth}. Sampled: {sampled}. Reuse:{" "}
            <a
              href={
                data.sources?.taiwan?.licence_url ??
                "https://data.gov.tw/license"
              }
              target="_blank"
              rel="noreferrer"
            >
              OGDL 1.0
            </a>{" "}
            and{" "}
            <a
              href={
                data.sources?.korea?.licence_url ??
                "https://www.customs.go.kr/kcs/cm/cntntsView.do?cntntsId=2611&mi=7422"
              }
              target="_blank"
              rel="noreferrer"
            >
              KCS public data policy
            </a>
            .
          </p>
        </div>
      </div>
    </>
  );
}
