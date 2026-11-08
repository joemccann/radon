"use client";

import { useMemo, useState } from "react";
import { Activity } from "lucide-react";
import styles from "./ImbalancePanel.module.css";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import FreshnessRail from "./FreshnessRail";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import RequestError from "./RequestError";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import { presetRange, type RangePresetSlug } from "@/lib/historyRange";
import { formatEma, formatSpx, formatTrades, type ImbalancePoint } from "@/lib/imbalance";
import { IMBALANCE_REFRESH } from "@/lib/refreshSchedule";
import { useImbalance } from "@/lib/useImbalance";

const TOOLTIP =
  "Daily US-wide print count at the ask minus print count at the bid. TradingView labels these NUMBER OF TRADES, not share volume. Universe, session coverage, and tick-rule classification are unverified. Missing sessions stay blank, never zero.";

type Row = ImbalancePoint & { positive: number | null; negative: number | null };
const monthTick = (date: Date) =>
  date.toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" });
const finite = (value: number | null) =>
  value != null && Number.isFinite(value) ? value : null;

export default function ImbalancePanel() {
  const { data, loading, syncing, error, syncNow } = useImbalance();
  const series = data?.series ?? [];
  const [range, setRange] = useState<RangePresetSlug | "custom">("1y");
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);
  const total = series.length;
  const chartRange = useMemo<[number, number]>(() => {
    if (total < 2) return [0, Math.max(total - 1, 0)];
    if (range === "custom" && customRange) {
      const max = total - 1;
      const end = Math.min(customRange[1], max);
      const start = Math.max(0, Math.min(customRange[0], end));
      return [start, end];
    }
    return presetRange(range === "custom" ? "1y" : range, total);
  }, [range, customRange, total]);
  const rows = useMemo(() => {
    const [start, end] = chartRange;
    return series.slice(start, end + 1).map((point): Row => {
      const net = finite(point.imbalance);
      return {
        ...point,
        spx_close: finite(point.spx_close),
        ema10: finite(point.ema10),
        positive: net != null && net >= 0 ? net : null,
        negative: net != null && net < 0 ? net : null,
      };
    });
  }, [series, chartRange]);
  const requestError = <RequestError error={error} onRetry={syncNow} retainedData={!!data?.current} />;
  if ((loading || syncing) && !data) {
    return <>{requestError}<SpectralLoader label="Loading ASK-BID trade-count imbalance" /></>;
  }
  if (!data?.current || data.missing) {
    return <>{requestError}<SectionEmptyState icon={Activity}
      headline="No aligned ASK-BID trade-count session yet"
      secondary="A completed session requires TradingView USI TRDS.ASK.US and TRDS.BID.US daily closes. The daily collector supplies these measurements." /></>;
  }
  const current = data.current;
  const unitsVerified = data.source?.units_verified === true;
  const positive: ChartSeries<Row> = {
    key: "positive", label: "ASK-BID (trades)", color: chartSeriesColor("primary"),
    axis: "left", renderAs: "bar",
    axisFormat: (value) => formatTrades(value), format: (value) => formatTrades(value),
  };
  const negative: ChartSeries<Row> = {
    key: "negative", label: "ASK-BID (trades)", color: chartSeriesColor("fault"),
    axis: "left", renderAs: "bar",
    axisFormat: (value) => formatTrades(value), format: (value) => formatTrades(value),
  };
  const spx: ChartSeries<Row> = {
    key: "spx_close", label: "SPX close", color: chartSeriesColor("primary"),
    axis: "left", axisFormat: (value) => value.toFixed(0), format: (value) => formatSpx(value),
  };
  const available = series.filter((point) => point.imbalance != null);
  return <>
    {requestError}
    <div className="section" data-testid="imbalance-panel">
      <div className="section-header">
        <div className="section-title">
          <Activity size={14} />Imbalance
          <InfoTooltip text={TOOLTIP} />
        </div>
      </div>
      <div className={styles.metrics}><RegimeStrip>
        <RegimeStripCell testId="imbalance-strip-net" label="ASK-BID TRADES"
          value={<span data-testid="imbalance-net">{formatTrades(current.imbalance)}</span>}
          sub={<>ASK {formatTrades(current.ask)} / BID {formatTrades(current.bid)}</>} />
        <RegimeStripCell testId="imbalance-strip-ema" label="EMA 10"
          value={<span data-testid="imbalance-ema">{formatEma(current.ema10)}</span>}
          sub="Close of ASK-BID, TradingView seed" />
        <RegimeStripCell testId="imbalance-strip-spx" label="SPX CLOSE"
          value={<span data-testid="imbalance-spx">{formatSpx(current.spx_close)}</span>} />
      </RegimeStrip></div>
      <FreshnessRail schedule={IMBALANCE_REFRESH} asOf={data.data_date} testId="imbalance-freshness" />
      <div className="section-footnote" data-testid="imbalance-source">
        TradingView USI trade counts (NUMBER OF TRADES AT ASK/BID), not share volume.
        {unitsVerified ? " Units verified against public USI titles and ASK-BID = BATD." : " Units unverified."}
        {" Universe, session coverage, and tick-rule classification are unverified."}
        {data.source?.history_complete === false && " Available history is incomplete."}
        {data.source?.lagging && ` Latest expected session ${data.source.expected_session_date} is incomplete; showing the last aligned session.`}
        {data.source?.source_finality === "unverified" && " Provider finality is unverified."}
      </div>
    </div>
    <div className="breadth-history-block" data-testid="imbalance-chart-section">
      <HistoryRangeChips
        active={range}
        onChange={(slug) => { setCustomRange(null); setRange(slug); }}
        maxSessions={series.length}
        ariaLabel="Imbalance chart range"
        dataTestId="imbalance-range"
      />
      <CriHistoryChart
        history={rows} series={[positive, negative]} sharedAxis
        title="ASK-BID TRADE COUNT" xTickFormat={monthTick}
        referenceLevels={[{ value: 0, label: "ZERO" }]}
        maxGapMs={5 * 86_400_000}
      />
      <CriHistoryChart
        history={rows} series={[spx]} title="S&P 500 INDEX"
        xTickFormat={monthTick} maxGapMs={5 * 86_400_000}
      />
      {total >= 2 && (
        <BrushMinimap
          values={series.map((point) => point.imbalance)}
          range={chartRange}
          onRangeChange={(next) => setCustomRange(next)}
          onCustom={() => setRange("custom")}
          testIdPrefix="imbalance-brush"
          ariaLabel="Imbalance history range brush"
        />
      )}
      <details className="section-footnote" data-testid="imbalance-methodology">
        <summary>Sources and units</summary>
        <p>Aligned coverage: {available[0]?.date ?? "---"} to {available.at(-1)?.date ?? "---"}, {available.length.toLocaleString("en-US")} sessions. Latest aligned session: {data.data_date ?? "---"}.</p>
        <p>USI:TRDS.ASK.US is US stocks number of trades at ask. USI:TRDS.BID.US is number of trades at bid. Published imbalance is ASK close minus BID close. USI:BATD.US is a parity check only and is never substituted. SP:SPX is the S&P 500 index. Share-volume symbols USI:VOL.ASK.US, USI:VOL.BID.US, and USI:BAVD.US are not used.</p>
        <p>EMA(10) is TradingView-style (SMA seed, k=2/11) over finite ASK-BID closes. It is shown in the strip because the histogram already uses both chart series slots for signed bars.</p>
        <p>Requested by Joe McCann after https://x.com/benkizemchuk/status/2119045970805604478. The public excerpt called this share volume; the chart formula and public USI titles are trade counts.</p>
        {!!data.source?.parity_mismatches?.length && (
          <p>{data.source.parity_mismatches.length.toLocaleString("en-US")} sessions disagree with BATD; ASK-BID remains the published value.</p>
        )}
        {!!data.source?.invalid_observations?.length && (
          <p>{data.source.invalid_observations.length.toLocaleString("en-US")} invalid source observations are omitted and remain gaps.</p>
        )}
      </details>
    </div>
  </>;
}
