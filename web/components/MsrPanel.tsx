"use client";

import type { CSSProperties } from "react";

import { useCallback, useMemo, useState } from "react";
import { Zap } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import FreshnessRail from "./FreshnessRail";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SortTh from "./SortTh";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import { presetRange, type RangePresetSlug } from "@/lib/historyRange";
import {
  MSR_INDEXES,
  buildMsrChartRows,
  formatMovePct,
  formatMsr,
  formatRank,
  formatSourceDate,
  msrTone,
  type MsrChartRow,
  type MsrIndex,
  type MsrIndexSummary,
  type MsrTopEntry,
} from "@/lib/msr";
import { MSR_REFRESH } from "@/lib/refreshSchedule";
import { useMsr } from "@/lib/useMsr";
import { useSort } from "@/lib/useSort";
import { useViewport } from "@/lib/useViewport";

const MSR_TOOLTIP =
  "Absolute 1-day % move divided by the prior close's 1-month implied vol index (VIX for SPX, VXN for NDX, RVX for RUT). Readings above the 99th percentile mean the vol market badly underpriced the move. Uses the Cboe vol index, which runs about 1.2x at-the-money implied vol, so levels sit below the Goldman series while ranks match.";

const SOURCE_FOOTNOTE =
  "Cboe index and vol index daily closes; NDX and RUT closes from IB with Cboe and Yahoo fallbacks. Ranks and percentiles span every session since the series start, not the visible range.";

const DEFAULT_INDEX: MsrIndex = "SPX";
const DEFAULT_PRESET: RangePresetSlug = "all";
const MIN_CHART_POINTS = 2;
const Y_HEADROOM = 1.1;

type TopSortKey = "rank" | "date" | "msr" | "move";

type RankedTopEntry = MsrTopEntry & { rank: number };

/** Daily-series x-tick over a multi-year span: "05 Nov 26". */
function formatDayTick(d: Date): string {
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "2-digit",
    timeZone: "UTC",
  });
}

function metricTone(color: string): "neg" | "warn" | "mut" {
  if (color === "var(--negative)") return "neg";
  if (color === "var(--warning)") return "warn";
  return "mut";
}

function rankTopEntries(top: ReadonlyArray<MsrTopEntry>): RankedTopEntry[] {
  return top.map((entry, i) => ({ ...entry, rank: i + 1 }));
}

function topSortValue(entry: RankedTopEntry, key: TopSortKey): string | number {
  if (key === "date") return entry.date;
  if (key === "msr") return entry.msr;
  if (key === "move") return entry.move_pct;
  return entry.rank;
}

function clampRange(range: [number, number], total: number): [number, number] {
  const max = total - 1;
  const end = Math.min(range[1], max);
  const start = Math.max(0, Math.min(range[0], end));
  return [start, end];
}

function zeroFloorDomain(rows: ReadonlyArray<MsrChartRow>): [number, number] | undefined {
  const peak = rows.reduce((m, r) => Math.max(m, r.msr), 0);
  return peak > 0 ? [0, peak * Y_HEADROOM] : undefined;
}

function IndexChips({ active, onChange }: { active: MsrIndex; onChange: (index: MsrIndex) => void }) {
  return (
    <nav className="history-range-chips history-range-chips--inline" aria-label="MSR index" data-testid="msr-index-chips">
      {MSR_INDEXES.map((index) => (
        <button
          key={index}
          type="button"
          className={`history-range-chip${active === index ? " is-active" : ""}`}
          aria-pressed={active === index}
          onClick={() => onChange(index)}
          data-testid={`msr-index-${index}`}
        >
          {index}
        </button>
      ))}
    </nav>
  );
}

function MsrStrip({
  summary,
  sourceDate,
  compact,
}: {
  summary: MsrIndexSummary;
  sourceDate: string;
  compact: boolean;
}) {
  const { current, week_max: weekMax, stats, iv_symbol: ivSymbol } = summary;
  const lastColor = msrTone(current?.msr, stats);
  const lastValue = formatMsr(current?.msr);
  const rank = formatRank(current?.rank, stats?.count);
  const weekMaxValue = formatMsr(weekMax?.msr);
  const weekMaxRank = weekMax ? `#${weekMax.rank.toLocaleString("en-US")}` : "---";
  const ivLabel = `${ivSymbol} PRIOR`;
  const ivPrior = current ? current.iv_prior.toFixed(2) : "---";

  if (compact) {
    return (
      <div className="m-regime-grid2x2" data-testid="msr-mobile-grid">
        <MetricCell label="LAST MSR" value={lastValue} tone={metricTone(lastColor)} testId="msr-mobile-last" />
        <MetricCell label="RANK" value={rank} />
        <MetricCell
          label="5D MAX"
          value={`${weekMaxValue} ${weekMaxRank}`}
          tone={metricTone(msrTone(weekMax?.msr, stats))}
          testId="msr-mobile-week-max"
        />
        <MetricCell label="MAX" value={formatMsr(stats?.max)} />
        <MetricCell label="P90" value={formatMsr(stats?.p90)} />
        <MetricCell label="P99" value={formatMsr(stats?.p99)} />
        <MetricCell label="MOVE" value={formatMovePct(current?.move_pct)} />
        <MetricCell label={ivLabel} value={ivPrior} />
        <MetricCell label="SOURCE UPDATED" value={sourceDate} />
      </div>
    );
  }

  return (
    <div className="regime-strip-fit" style={{ "--fit-cols": 3, "--fit-cols-md": 3 } as CSSProperties}>
      <RegimeStrip>
        <RegimeStripCell
          testId="msr-strip-last"
          label="LAST MSR"
          value={<span data-testid="msr-last-value" style={{ color: lastColor }}>{lastValue}</span>}
          sub={<>{current?.date ?? "NO SESSION"}</>}
        />
        <RegimeStripCell testId="msr-strip-rank" label="RANK" value={rank} sub={<>1 IS THE LARGEST SURPRISE</>} />
        <RegimeStripCell
          testId="msr-strip-week-max"
          label="5D MAX"
          value={
            <span data-testid="msr-week-max" style={{ color: msrTone(weekMax?.msr, stats) }}>
              {weekMaxValue} <span style={{ color: "var(--text-muted)" }}>{weekMaxRank}</span>
            </span>
          }
          sub={<>{weekMax?.date ?? "TRAILING 5 SESSIONS"}</>}
        />
        <RegimeStripCell testId="msr-strip-max" label="MAX" value={formatMsr(stats?.max)} sub={<>{stats?.max_date ?? "---"}</>} />
        <RegimeStripCell testId="msr-strip-p90" label="P90" value={formatMsr(stats?.p90)} sub={<>WARNING ABOVE</>} />
        <RegimeStripCell testId="msr-strip-p99" label="P99" value={formatMsr(stats?.p99)} sub={<>EXTREME ABOVE</>} />
        <RegimeStripCell testId="msr-strip-move" label="MOVE" value={formatMovePct(current?.move_pct)} sub={<>CLOSE TO CLOSE</>} />
        <RegimeStripCell testId="msr-strip-iv" label={ivLabel} value={ivPrior} sub={<>DIVISOR AT PRIOR CLOSE</>} />
        <RegimeStripCell
          testId="msr-strip-source"
          label="SOURCE UPDATED"
          value={sourceDate}
          sub={<>CBOE {ivSymbol} FILE</>}
        />
      </RegimeStrip>
    </div>
  );
}

function TopTable({ summary, index }: { summary: MsrIndexSummary; index: MsrIndex }) {
  const ranked = useMemo(() => rankTopEntries(summary.top), [summary.top]);
  const { sorted, sort, toggle } = useSort<RankedTopEntry, TopSortKey>(ranked, topSortValue, "rank");
  const header = (key: TopSortKey, label: string, className?: string) => (
    <SortTh<TopSortKey>
      label={label}
      sortKey={key}
      className={className}
      activeKey={sort.key}
      direction={sort.direction}
      onToggle={toggle}
    />
  );

  return (
    <div className="gex-history-table-wrap">
      <table className="gex-history-table" data-testid="msr-top-table" aria-label={`${index} largest move surprises`}>
        <thead>
          <tr>
            {header("rank", "RANK")}
            {header("date", "DATE")}
            {header("msr", "MSR", "text-right")}
            {header("move", "MOVE", "text-right")}
          </tr>
        </thead>
        <tbody>
          {sorted.map((entry) => (
            <tr key={entry.date}>
              <td>#{entry.rank}</td>
              <td>{entry.date}</td>
              <td className="text-right" style={{ color: msrTone(entry.msr, summary.stats) }}>{formatMsr(entry.msr)}</td>
              <td className="text-right">{formatMovePct(entry.move_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MsrChart({ rows, index }: { rows: MsrChartRow[]; index: MsrIndex }) {
  const [preset, setPreset] = useState<RangePresetSlug | "custom">(DEFAULT_PRESET);
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);
  const total = rows.length;

  const chartRange = useMemo<[number, number]>(() => {
    if (total < MIN_CHART_POINTS) return [0, Math.max(total - 1, 0)];
    if (preset === "custom" && customRange) return clampRange(customRange, total);
    return presetRange(preset === "custom" ? DEFAULT_PRESET : preset, total);
  }, [preset, customRange, total]);

  if (total < MIN_CHART_POINTS) {
    return <div className="section-footnote">No {index} readings to chart yet.</div>;
  }

  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);
  const chartSeries: [ChartSeries<MsrChartRow>] = [
    {
      key: "msr",
      label: "MSR",
      color: chartSeriesColor("primary"),
      axis: "right",
      scaleType: "linear",
      yDomain: zeroFloorDomain(slice),
      format: formatMsr,
    },
  ];

  return (
    <>
      <HistoryRangeChips
        active={preset}
        onChange={(slug) => {
          setCustomRange(null);
          setPreset(slug);
        }}
        maxSessions={total}
        ariaLabel="MSR chart range"
        dataTestId="msr-range-chips"
      />
      <CriHistoryChart
        history={slice}
        series={chartSeries}
        title={`${index} MOVE SURPRISE RATIO`}
        xTickFormat={formatDayTick}
      />
      <BrushMinimap
        values={rows.map((r) => r.msr)}
        range={chartRange}
        onRangeChange={(r) => setCustomRange(r)}
        onCustom={() => setPreset("custom")}
        testIdPrefix="msr-brush"
        ariaLabel="MSR history range brush"
      />
    </>
  );
}

export default function MsrPanel() {
  const { data, loading, syncing, lastSync } = useMsr();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;
  const [index, setIndex] = useState<MsrIndex>(DEFAULT_INDEX);
  const selectIndex = useCallback((next: MsrIndex) => setIndex(next), []);

  const series = data?.series;
  const rows = useMemo(() => buildMsrChartRows(series ?? [], index), [series, index]);

  if ((loading || syncing) && !data) {
    return <SpectralLoader label="Loading Cboe MSR series" />;
  }

  if (!data || data.missing || !data.indexes) {
    return (
      <SectionEmptyState
        icon={Zap}
        headline="No MSR data yet"
        secondary="The MSR refresh timer populates this tab from Cboe's index and vol index daily series. Data appears after the first successful pull."
      />
    );
  }

  const summary = data.indexes[index];
  const sourceDate = formatSourceDate(data.source_last_modified?.vix ?? null);

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Zap size={14} />
            Move Surprise Ratio
            <InfoTooltip text={MSR_TOOLTIP} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <IndexChips active={index} onChange={selectIndex} />
            {lastSync && (
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-meta)", color: "var(--text-muted)" }}>
                {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
              </span>
            )}
          </div>
        </div>

        <MsrStrip summary={summary} sourceDate={sourceDate} compact={compact} />

        <FreshnessRail
          schedule={MSR_REFRESH}
          asOf={summary.current?.date ?? null}
          testId="msr-freshness-rail"
          asOfTestId="msr-strip-asof"
        />
      </div>

      <div className="breadth-history-block" data-testid="msr-chart-section">
        <MsrChart key={index} rows={rows} index={index} />
        <TopTable key={`top-${index}`} summary={summary} index={index} />
        <div className="section-footnote">{SOURCE_FOOTNOTE}</div>
      </div>
    </>
  );
}
