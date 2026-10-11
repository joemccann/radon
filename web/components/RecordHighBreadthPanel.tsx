"use client";

import { useMemo, useState } from "react";
import { Activity } from "lucide-react";
import styles from "./RecordHighBreadthPanel.module.css";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import FreshnessRail from "./FreshnessRail";
import { RECORD_HIGH_BREADTH_REFRESH } from "@/lib/refreshSchedule";
import HistoryRangeChips from "./HistoryRangeChips";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import RequestError from "./RequestError";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import { presetRange, type RangePresetSlug } from "@/lib/historyRange";
import { breadthValue, type RecordHighBreadthPoint } from "@/lib/recordHighBreadth";
import { useRecordHighBreadth } from "@/lib/useRecordHighBreadth";

type Row = RecordHighBreadthPoint & { positive: number | null; negative: number | null };
const monthTick = (date: Date) => date.toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" });
const finite = (value: number | null) => value != null && Number.isFinite(value) ? value : null;

export default function RecordHighBreadthPanel() {
  const { data, loading, syncing, error, syncNow } = useRecordHighBreadth();
  const [range, setRange] = useState<RangePresetSlug>("1y");
  const series = data?.series ?? [];
  const rows = useMemo(() => {
    const [start, end] = presetRange(range, series.length);
    return series.slice(start, end + 1).map((point): Row => {
      const net = finite(point.net_high_low_per_500);
      return { ...point, spx_close: finite(point.spx_close), above_200_pct: finite(point.above_200_pct),
        positive: net != null && net >= 0 ? net : null, negative: net != null && net < 0 ? net : null };
    });
  }, [series, range]);
  const requestError = <RequestError error={error} onRetry={syncNow} retainedData={!!data?.current} />;
  if ((loading || syncing) && !data) return <>{requestError}<SpectralLoader label="Loading S&P 500 breadth history" /></>;
  if (!data?.current || data.missing) return <>{requestError}<SectionEmptyState icon={Activity}
    headline="No aligned S&P 500 breadth session yet"
    secondary="A completed session requires SPX, stocks above their 200-day average, and both 52-week high and low counts. The daily collector supplies these measurements." /></>;
  const current = data.current;
  const spx: ChartSeries<Row> = { key: "spx_close", label: "SPX close", color: chartSeriesColor("primary"), axis: "left", format: (value) => breadthValue(value) };
  const above: ChartSeries<Row> = { key: "above_200_pct", label: "Above 200-day SMA", color: chartSeriesColor("primary"), axis: "left", format: (value) => breadthValue(value, "%") };
  const positive: ChartSeries<Row> = { key: "positive", label: "Net highs (% of 500)", color: chartSeriesColor("primary"), axis: "left", renderAs: "bar", format: (value) => breadthValue(value, "%") };
  const negative: ChartSeries<Row> = { key: "negative", label: "Net lows (% of 500)", color: chartSeriesColor("fault"), axis: "left", renderAs: "bar", format: (value) => breadthValue(value, "%") };
  const parity = data.source?.parity;
  const available = series.filter(point => point.spx_close != null && point.above_200_pct != null && point.net_high_low_per_500 != null);
  return <>
    {requestError}
    <div className="section" data-testid="record-high-breadth-panel">
      <div className="section-header"><div className="section-title"><Activity size={14} />S&P 500 Breadth</div></div>
      <div className={styles.metrics}><RegimeStrip>
        <RegimeStripCell testId="record-high-breadth-strip-spx" label="SPX CLOSE" value={<span data-testid="record-high-breadth-spx">{breadthValue(current.spx_close)}</span>} />
        <RegimeStripCell testId="record-high-breadth-strip-above" label="ABOVE 200-DAY SMA" value={<span data-testid="record-high-breadth-above">{breadthValue(current.above_200_pct, "%")}</span>} />
        <RegimeStripCell testId="record-high-breadth-strip-net" label="NET HIGH-LOW % (500 BASE)" value={<span data-testid="record-high-breadth-net">{breadthValue(current.net_high_low_per_500, "%")}</span>} sub={<>Highs {current.new_highs ?? "---"} / lows {current.new_lows ?? "---"}</>} />
        <RegimeStripCell testId="record-high-breadth-strip-date" label="ALIGNED SESSION" value={data.data_date ?? "---"} />
      </RegimeStrip></div>
      <FreshnessRail schedule={RECORD_HIGH_BREADTH_REFRESH} asOf={data.data_date} testId="record-high-breadth-freshness" />
      <div className="section-footnote" data-testid="record-high-breadth-source">
        TradingView source variant. The net count is normalized per 500; the actual issue denominator is unverified.
        {data.source?.history_complete === false && " Available history is incomplete; comparisons with 1973 and 1999 are unverified."}
        {data.source?.lagging && ` Latest expected session ${data.source.expected_session_date} is incomplete; showing the last aligned session.`}
        {data.source?.source_finality === "unverified" && " Provider finality is unverified."}
        {data.source?.latest_observed_date && data.source?.latest_observed_date !== data.data_date && ` Latest observed session ${data.source?.latest_observed_date} has incomplete measurements.`}
      </div>
    </div>
    <div className="breadth-history-block" data-testid="record-high-breadth-chart-section">
      <HistoryRangeChips active={range} onChange={setRange} maxSessions={series.length} ariaLabel="S&P 500 breadth chart range" dataTestId="record-high-breadth-range" />
      <CriHistoryChart history={rows} series={[spx]} title="S&P 500 INDEX" xTickFormat={monthTick} maxGapMs={5 * 86_400_000} />
      <CriHistoryChart history={rows} series={[above]} title="S&P 500 STOCKS ABOVE 200-DAY SMA" xTickFormat={monthTick} maxGapMs={5 * 86_400_000} />
      <CriHistoryChart history={rows} series={[positive, negative]} sharedAxis title="NET HIGH-LOW % (500 BASE)" xTickFormat={monthTick} referenceLevels={[{ value: 0, label: "ZERO" }]} />
      <details className="section-footnote" data-testid="record-high-breadth-methodology">
        <summary>Sources and reference parity</summary>
        <p>Aligned coverage: {available[0]?.date ?? "---"} to {available.at(-1)?.date ?? "---"}, {available.length.toLocaleString("en-US")} sessions. Latest aligned session: {data.data_date ?? "---"}.</p>
        <p>SP:SPX is the S&P 500 index. INDEX:S5TH measures stocks above their 200-day average. INDEX:MAHP and INDEX:MALP supply 52-week high and low counts. Net High-Low % (500 base) = 100 × (highs − lows) / 500.</p>
        {!!data.source?.invalid_observations?.length && <p>{data.source.invalid_observations.length.toLocaleString("en-US")} non-integer historical count observations are omitted from derived breadth and remain gaps.</p>}
        <p>The original StockCharts High-Low Percent divides by total issues. The fixed normalization here is a source variant, and is not verified as that original percentage.</p>
        {parity && parity.tradingview && <p>Reference {parity.reference_date}: StockCharts SPX {breadthValue(parity.reference.spx_close)}, above 200-day {breadthValue(parity.reference.above_200_pct, "%")}, High-Low Percent {breadthValue(parity.reference.net_high_low_pct, "%")}. TradingView SPX {breadthValue(parity.tradingview.spx_close)}, above 200-day {breadthValue(parity.tradingview.above_200_pct, "%")}, net (500 base) {breadthValue(parity.tradingview.net_high_low_per_500, "%")}. Source parity: {parity.status}.</p>}
      </details>
    </div>
  </>;
}
