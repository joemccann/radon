"use client";

import { useMemo, useState } from "react";
import { Activity } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import FreshnessRail from "./FreshnessRail";
import HistoryRangeChips from "./HistoryRangeChips";
import RequestError from "./RequestError";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import { highLowLineZone, type HighLowLineInterval } from "@/lib/highLowLine";
import { RANGE_PRESETS, type RangePresetSlug } from "@/lib/historyRange";
import { HIGH_LOW_LINE_REFRESH } from "@/lib/refreshSchedule";
import { useHighLowLine } from "@/lib/useHighLowLine";

const PRESETS = RANGE_PRESETS.filter(p => ["1y", "3y", "5y", "all"].includes(p.slug));
const finite = (v: number | null | undefined): v is number => v != null && Number.isFinite(v);
const number = (v: number | null | undefined) => finite(v) ? v.toLocaleString("en-US", { maximumFractionDigits: 4 }) : "---";
const signed = (v: number | null | undefined) => finite(v) ? `${v > 0 ? "+" : ""}${number(v)}` : "---";
const compact = (v: number) => Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(0)}k` : v.toFixed(0);
const tickDate = (d: Date) => d.toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" });

export default function HighLowLinePanel() {
  const { data, loading, syncing, error, syncNow } = useHighLowLine();
  const [interval, setInterval] = useState<HighLowLineInterval>("weekly");
  const [preset, setPreset] = useState<RangePresetSlug | "custom">("5y");
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);
  // Both charts receive these same complete, precomputed observations. Range
  // controls never recompute RSI or restart the cumulative baseline.
  const rows = useMemo(() => (interval === "daily" ? data?.series : data?.weekly_series ?? [])?.filter(row => Number.isFinite(Date.parse(row.date))).map(row => ({
    ...row,
    cumulative: finite(row.cumulative) ? row.cumulative : null,
    sar: finite(row.sar) ? row.sar : null,
    stoch_rsi14: highLowLineZone(row.stoch_rsi14) === "unavailable" ? null : row.stoch_rsi14,
  })) ?? [], [data, interval]);
  const range = useMemo<[number, number]>(() => {
    const last = Math.max(0, rows.length - 1);
    if (preset === "custom" && customRange) return [Math.max(0, Math.min(customRange[0], last)), Math.max(0, Math.min(customRange[1], last))];
    if (!rows.length || preset === "all") return [0, last];
    const years = preset === "1y" ? 1 : preset === "3y" ? 3 : 5;
    const cutoff = new Date(`${rows[last].date}T00:00:00Z`);
    cutoff.setUTCFullYear(cutoff.getUTCFullYear() - years);
    return [Math.max(0, rows.findIndex(row => Date.parse(row.date) >= cutoff.getTime())), last];
  }, [rows, preset, customRange]);

  if ((loading || syncing) && !data) return <SpectralLoader label="Loading NYSE common-stock high-low series" />;
  if (!data || data.missing || !data.current || !data.series.length) return <>
    <RequestError error={error} onRetry={syncNow} />
    <SectionEmptyState icon={Activity} headline="Awaiting NYSE common-stock High-Low Line" secondary="This panel needs a successful sample of StockCharts DecisionPoint common-stock-only new highs and lows." />
  </>;

  const current = interval === "daily" ? data.current : data.weekly_current;
  const forming = interval === "weekly" && current?.period_complete === false;
  const zone = highLowLineZone(current?.stoch_rsi14 ?? null);
  const sampleDate = data.scan_time ? new Date(data.scan_time) : null;
  const sample = sampleDate && Number.isFinite(sampleDate.getTime()) ? `${sampleDate.toISOString().slice(0, 19).replace("T", " ")} UTC` : "---";
  const history = rows.slice(range[0], range[1] + 1);
  type Row = (typeof rows)[number];
  const cumulative: [ChartSeries<Row>, ChartSeries<Row>] = [
    { key: "cumulative", label: "HIGH-LOW LINE", axis: "left", color: chartSeriesColor("primary"), format: number, axisFormat: compact, pointMarkers: "isolated" },
    { key: "sar", label: "SAR (1, 1)", axis: "right", color: chartSeriesColor("comparison"), format: number, axisFormat: compact, renderAs: "points" },
  ];
  const stoch: [ChartSeries<Row>] = [{ key: "stoch_rsi14", label: "RAW STOCHRSI (14)", axis: "left", color: chartSeriesColor("primary"), format: v => v.toFixed(3), yDomain: [0, 1], pointMarkers: "isolated" }];

  return <>
    <RequestError error={error} onRetry={syncNow} retainedData />
    <section className="section" data-testid="high-low-line-panel">
      <div className="section-header"><div className="section-title"><Activity size={14} />NYSE common-stock High-Low Line</div></div>
      <nav className="history-range-chips" aria-label="High-Low calculation interval">
        {(["daily", "weekly"] as const).map(value => <button key={value} type="button" className={`history-range-chip${interval === value ? " is-active" : ""}`} aria-pressed={interval === value} onClick={() => { setInterval(value); setCustomRange(null); if (preset === "custom") setPreset("5y"); }}>{value === "daily" ? "Daily" : "Weekly"}</button>)}
      </nav>
      <RegimeStrip columns={4}>
        <RegimeStripCell testId="high-low-line-strip-cumulative" label="CUMULATIVE" value={<span style={{ fontSize: "clamp(14px, 2vw, 28px)", whiteSpace: "nowrap" }}>{number(current?.cumulative)}</span>} sub="NYSE COMMON STOCK ONLY" />
        <RegimeStripCell testId="high-low-line-strip-net" label={interval === "weekly" ? "WEEK NET" : "DAILY NET"} value={signed(current?.net)} sub={`${number(current?.highs)} HIGHS / ${number(current?.lows)} LOWS`} />
        <RegimeStripCell testId="high-low-line-strip-stoch" label="STOCHRSI (14)" value={finite(current?.stoch_rsi14) && zone !== "unavailable" ? current.stoch_rsi14.toFixed(3) : "---"} sub={`${zone.toUpperCase()}${current?.reset_to_zero ? " · RESET TO ZERO" : ""}${forming ? " · PROVISIONAL" : ""}`} />
        <RegimeStripCell testId="high-low-line-strip-sar" label="SAR DIRECTION" value={current?.sar_direction?.toUpperCase() ?? "---"} sub="START / INCREMENT / MAX 1.0" />
      </RegimeStrip>
      <FreshnessRail schedule={HIGH_LOW_LINE_REFRESH} asOf={data.data_date} asOfTestId="high-low-line-strip-asof" testId="high-low-line-freshness-rail" />
      <div className="chart-footnote" style={{ fontSize: 12 }} data-testid="high-low-line-sample">Sampled {sample} · {interval === "weekly" ? "Weekly" : "Daily"} calculation as of {current?.date ?? "---"}{forming ? <span data-testid="high-low-line-week-progress"> · Week in progress; zero/reset conditions are provisional.</span> : null}</div>
      {data.degraded || data.stale ? <p className="chart-footnote" style={{ color: "var(--warning)", fontSize: 12 }} data-testid="high-low-line-degraded">{data.stale ? "Writer sample is stale. " : ""}{data.degraded ? `Source observations lag the expected completed session ${data.expected_session_date ?? "---"}.` : ""}</p> : null}
    </section>
    <section className="breadth-history-block" data-testid="high-low-line-chart-section">
      <HistoryRangeChips presets={PRESETS} active={preset} onChange={setPreset} ariaLabel="High-Low chart range" dataTestId="high-low-line-range-chips" />
      <div data-testid="high-low-line-cumulative-chart"><CriHistoryChart history={history} series={cumulative} sharedAxis title="CUMULATIVE COMMON-STOCK HIGH-LOW LINE" xTickFormat={tickDate} xTickMinSpacing={90} /></div>
      <div data-testid="high-low-line-stoch-chart"><CriHistoryChart history={history} series={stoch} title="RAW STOCHRSI (14)" xTickFormat={tickDate} xTickMinSpacing={90}
        referenceLevels={[{ value: 0.2, label: "" }, { value: 0.5, label: "0.5" }, { value: 0.8, label: "0.8" }]}
        referenceBands={[{ from: 0, to: 0.2, label: "OVERSOLD", axis: "left", color: "var(--warning)" }, { from: 0.8, to: 1, label: "OVERBOUGHT", axis: "left", color: "var(--text-secondary)" }]} /></div>
      <BrushMinimap values={rows.map(row => row.cumulative)} range={range} onRangeChange={setCustomRange} onCustom={() => setPreset("custom")} testIdPrefix="high-low-line-brush" formatIndex={index => rows[index]?.date ?? ""} />
      <div className="chart-footnote" style={{ fontSize: 12 }} data-testid="high-low-line-notes">
        <p>Source: StockCharts DecisionPoint !NEWHINYC minus !NEWLONYC, NYSE common stock only. Baseline: zero before the first included observation on {data.baseline?.date ?? "2003-01-02"}. Published fractional breadth values are preserved; {number(data.source?.fractional_observations)} fractional observations. Weekly totals sum daily readings, not distinct stocks.</p>
        <p>Wilder RSI (14), raw StochRSI (14), no K/D smoothing. SAR start, increment and maximum are 1.0. {interval === "daily" ? "Daily SAR uses close-only OHLC." : "Weekly SAR uses the range of daily cumulative closes."} This construction does not establish vendor parity. Zero and oversold readings alone do not establish a buying opportunity.</p>
        <p>Next sample is Radon&apos;s daily 00:30 UTC check, including weekends, not a verified source publication time.</p>
      </div>
    </section>
  </>;
}
