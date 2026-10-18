import type { AiHistoryPoint, AiIndicator, AiPane, AiSnapshot, AiSource } from "@/lib/aiInfrastructure";
import { marketDateKey, shiftDateKey } from "./time";

const SOURCE_ID = "radon_demo_sample";

type SampleMeasure = {
  id: string;
  pane: AiPane;
  title: string;
  label: string;
  unit: string;
  start: number;
  step: number;
};

// Ids sit outside the live catalog (D*, C*, H*, F*) so the panel uses this
// sample's own copy instead of describing a real vendor's methodology.
const MEASURES: SampleMeasure[] = [
  { id: "SD1", pane: "demand", title: "Sample usage index", label: "sample_usage_index", unit: "index", start: 100, step: 3.2 },
  { id: "SC1", pane: "compute", title: "Sample compute price", label: "sample_gpu_hour_price", unit: "USD/hour", start: 2.4, step: -0.03 },
  { id: "SB1", pane: "delivery", title: "Sample capacity delivered", label: "sample_capacity_gw", unit: "GW", start: 4.1, step: 0.18 },
  { id: "SF1", pane: "finance", title: "Sample capex coverage", label: "sample_capex_coverage", unit: "ratio", start: 1.32, step: -0.015 },
];

function round(value: number): number {
  return Math.round(value * 1_000) / 1_000;
}

/** Request-time AI infrastructure sample for /api/ai-cycle. No FastAPI or vendor fetch. */
export function buildDemoAiCycle(now: Date = new Date()): AiSnapshot {
  const generatedAt = now.toISOString();
  const today = marketDateKey(now);
  const weeks = Array.from({ length: 12 }, (_, index) => shiftDateKey(today, -7 * (11 - index)));

  const indicators: AiIndicator[] = MEASURES.map((measure) => {
    const history: AiHistoryPoint[] = weeks.map((date, index) => ({
      date,
      value: round(measure.start + measure.step * index + Math.sin(index / 2) * Math.abs(measure.step)),
      unit: measure.unit,
      series_id: measure.label,
      label: measure.label,
      source_id: SOURCE_ID,
    }));
    const latest = history[history.length - 1];
    return {
      id: measure.id,
      title: measure.title,
      pane: measure.pane,
      priority: "sample",
      status: "available",
      reason: "Sample data generated for the Radon demo. Not a market observation.",
      methodology: "Deterministic sample series regenerated on each request.",
      tickers: [],
      source_ids: [SOURCE_ID],
      metrics: [{
        id: `${measure.id}-latest`,
        label: measure.label,
        value: latest.value,
        unit: measure.unit,
        period_start: weeks[weeks.length - 2],
        period_end: latest.date,
        published_at: generatedAt,
        fetched_at: generatedAt,
        source_id: SOURCE_ID,
        source_url: "",
        measurement: "sample",
        methodology_version: "sample",
        cohort_version: "sample",
        lineage_group: "sample",
        raw_hash: `sample-${measure.id}-${latest.date}`,
        metadata: { description: "Sample data generated for the Radon demo." },
      }],
      history,
      history_coverage: [{
        source_id: SOURCE_ID,
        series_id: measure.label,
        unit: measure.unit,
        observation_count: history.length,
        displayed_count: history.length,
        observed_from: weeks[0],
        observed_through: latest.date,
        cadence: "weekly",
      }],
      history_series_count: 1,
    };
  });

  const sources: AiSource[] = [{
    id: SOURCE_ID,
    name: "Sample data",
    url: "",
    status: "available",
    reason: "Generated for the Radon demo at request time.",
    checked_at: generatedAt,
    cadence: "weekly",
    license: "Sample",
    lineage_group: "sample",
    observation_count: indicators.length * weeks.length,
    observed_from: weeks[0],
    observed_through: today,
  }];

  return {
    version: 1,
    generated_at: generatedAt,
    as_of: today,
    indicators,
    sources,
    shadow: {
      status: "experimental",
      state: "insufficient_evidence",
      reason: "Sample data does not support a shadow reading.",
      evaluated_at: generatedAt,
      eligible_weeks: 0,
    },
  };
}
