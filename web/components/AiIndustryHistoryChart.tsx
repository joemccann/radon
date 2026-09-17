"use client";

import { useMemo, useState } from "react";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import HistoryRangeChips from "./HistoryRangeChips";
import BrushMinimap from "./BrushMinimap";
import { aiNumber, aiObservationDate, type AiHistoryPoint } from "@/lib/aiInfrastructure";
import { aiCalendarPresets, aiCalendarRange, aiHistoryGapMs, aiPeriodicBars, comparableAiHistory } from "@/lib/aiIndustryHistory";
import type { RangePresetSlug } from "@/lib/historyRange";
import { chartSeriesColor } from "@/lib/chartSystem";
import styles from "./AiIndustryHistoryChart.module.css";

export interface AiIndustryHistoryChartProps { points: AiHistoryPoint[]; cadence?: string; sourceLabel?: string }

export default function AiIndustryHistoryChart(props: AiIndustryHistoryChartProps) {
  const identity = props.points[0];
  return <History key={`${identity?.source_id}:${identity?.series_id}:${identity?.unit}`} {...props} />;
}

function History({ points, cadence, sourceLabel }: AiIndustryHistoryChartProps) {
  const history = useMemo(() => comparableAiHistory(points), [points]);
  const [preset, setPreset] = useState<RangePresetSlug | "custom">("all");
  const [custom, setCustom] = useState<[number, number] | null>(null);
  const [inspection, setInspection] = useState<number | null>(null);
  const total = history.length;
  const range = useMemo<[number, number]>(() => {
    if (preset !== "custom" || !custom) return aiCalendarRange(history, preset === "custom" ? "all" : preset);
    const end = Math.max(0, Math.min(total - 1, custom[1]));
    return [Math.max(0, Math.min(end - 1, custom[0])), end];
  }, [custom, history, preset, total]);
  const slice = useMemo(() => history.slice(range[0], range[1] + 1), [history, range]);
  const identity = history[0];
  const definition = useMemo<[ChartSeries<AiHistoryPoint>]>(() => [{ key: "value", label: identity?.label ?? "Observation", color: chartSeriesColor("primary"), axis: "left", format: v => `${aiNumber(v)} ${identity?.unit ?? ""}`, axisFormat: v => new Intl.NumberFormat("en-US", { notation: "compact", maximumSignificantDigits: 3 }).format(v), renderAs: aiPeriodicBars(history, cadence) ? "bar" : "line" }], [cadence, history, identity]);
  const presets = useMemo(() => aiCalendarPresets(history), [history]);
  const gap = useMemo(() => aiHistoryGapMs(history, cadence), [history, cadence]);
  const values = useMemo(() => history.map(p => p.value), [history]);
  if (!identity) return <p className={styles.note}>No comparable observations. A chart requires one source, series and unit.</p>;
  if (total < 2) return <p className={styles.note}>One observation: {aiNumber(identity.value)} {identity.unit} · {aiObservationDate(identity.date)}. A trend needs at least two comparable dates.</p>;
  const chosen = slice[Math.min(inspection ?? slice.length - 1, slice.length - 1)];
  return <div className={styles.history} data-testid="ai-industry-history-chart">
    <HistoryRangeChips active={preset} presets={presets} onChange={next => { setPreset(next); setCustom(null); setInspection(null); }} ariaLabel="AI observation history range" />
    <p className={styles.units}>{identity.unit} · {sourceLabel || identity.source_id}{cadence ? ` · ${cadence}` : ""}</p>
    <CriHistoryChart history={slice} series={definition} title={identity.label} maxGapMs={gap} xTickFormat={date => new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "2-digit", timeZone: "UTC" }).format(date)} xTickMinSpacing={110} />
    <BrushMinimap values={values} range={range} onRangeChange={next => { setCustom(next); setPreset("custom"); setInspection(null); }} onCustom={() => setPreset("custom")} ariaLabel="AI full history range brush" testIdPrefix="ai-history-brush" formatIndex={i => aiObservationDate(history[i]?.date)} />
    <div className={styles.range}><span>{aiObservationDate(slice[0]?.date)} – {aiObservationDate(slice.at(-1)?.date)}</span><span>{slice.length} of {total} observations</span></div>
    <p className={styles.note}>Drag the overview handles to change the visible range. Lines connect available observations and break across unusually long gaps. The overview spaces observations evenly; collection frequency is not market-session frequency.</p>
    <label className={styles.inspection}>Inspect observation<input type="range" min={0} max={slice.length - 1} value={Math.min(inspection ?? slice.length - 1, slice.length - 1)} onChange={e => setInspection(Number(e.target.value))} aria-valuetext={`${aiObservationDate(chosen?.date)}: ${aiNumber(chosen?.value ?? null)} ${identity.unit}`} /><output>{aiObservationDate(chosen?.date)} · {aiNumber(chosen?.value ?? null)} {identity.unit}</output></label>
  </div>;
}
