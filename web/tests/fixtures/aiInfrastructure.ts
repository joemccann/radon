import type { AiPane, AiSnapshot } from "../../lib/aiInfrastructure";
/** Synthetic only. No fixture observations are collected or persisted. */
export const aiFixture: AiSnapshot = {
  version: 1, generated_at: "2026-09-07T12:00:00Z", as_of: "2026-09-07T12:00:00Z",
  shadow: { status: "experimental", state: "insufficient_evidence", reason: "Four comparable weekly evaluations are required.", evaluated_at: "2026-09-07T12:00:00Z", eligible_weeks: 2 },
  sources: [{ id: "fixture", name: "Fixture publisher", url: "https://example.com/evidence", status: "available", reason: "Synthetic verification fixture", checked_at: "2026-09-07T12:00:00Z", cadence: "daily", license: "test only", lineage_group: "fixture-host", observation_count: 24, observed_from: "2026-09-01", observed_through: "2026-09-06" }],
  indicators: ([ ["D1", "Public routed activity", "demand", "tokens"], ["C1", "Matched GPU asking prices", "compute", "USD/GPU-hour"], ["P1", "Delivered capacity", "delivery", "MW"], ["F1", "Cash funding coverage", "finance", "ratio"] ] as const).map(([id, title, pane, unit]) => ({
    id, title, pane: pane as AiPane, priority: "P0", status: "available", reason: "Synthetic measurement fixture. Not live data.", methodology: "Matched complete-period observations with a frozen cohort.", tickers: ["NVDA"], source_ids: ["fixture"],
    metrics: [{ id: `${id}-series`, label: title, value: 42, unit, period_start: "2026-09-06", period_end: "2026-09-06", published_at: null, fetched_at: "2026-09-07T12:00:00Z", source_id: "fixture", source_url: "https://example.com/evidence", measurement: "observed", methodology_version: "1", cohort_version: "fixture-v1", lineage_group: "fixture-host", raw_hash: "a".repeat(64), metadata: { coverage_numerator: 4, coverage_denominator: 5, exclusions: ["Incomplete periods"] } }],
    history: Array.from({ length: 6 }, (_, i) => ({ date: `2026-09-0${i + 1}`, value: 30 + i * 2 + (i % 2 ? 4 : 0), unit, series_id: `${id}-series`, label: title, source_id: "fixture" })),
  })),
};
