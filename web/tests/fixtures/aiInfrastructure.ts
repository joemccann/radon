import type { AiPane, AiSnapshot } from "../../lib/aiInfrastructure";
/** Synthetic only. No fixture observations are collected or persisted. */
export const aiFixture: AiSnapshot = {
  version: 1, generated_at: "2026-09-07T12:00:00Z", as_of: "2026-09-07T12:00:00Z",
  shadow: { status: "experimental", state: "insufficient_evidence", reason: "Four comparable weekly evaluations are required.", evaluated_at: "2026-09-07T12:00:00Z", eligible_weeks: 2 },
  sources: [
    { id: "fixture", name: "Fixture publisher", url: "https://example.com/evidence", status: "available", reason: "Synthetic verification fixture", checked_at: "2026-09-07T12:00:00Z", cadence: "daily", license: "test only", lineage_group: "fixture-host", observation_count: 24, observed_from: "2026-09-01", observed_through: "2026-09-06" },
    { id: "ramp", name: "Ramp AI Index", url: "https://ramp.com/data/ai-index", status: "available", reason: "Curated published Ramp AI Index fixture", checked_at: "2026-09-07T12:00:00Z", cadence: "monthly", license: "Public published research", lineage_group: "ramp", observation_count: 128, observed_from: "2024-01-01", observed_through: "2026-08-31" },
  ],
  indicators: ([
    ["D1", "Public routed activity", "demand", "tokens", "fixture"],
    ["D5", "Ramp business AI spend", "demand", "USD/employee-month", "ramp"],
    ["C1", "Matched GPU asking prices", "compute", "USD/GPU-hour", "fixture"],
    ["P1", "Delivered capacity", "delivery", "MW", "fixture"],
    ["F1", "Cash funding coverage", "finance", "ratio", "fixture"],
  ] as const).map(([id, title, pane, unit, sourceId]) => ({
    id, title, pane: pane as AiPane, priority: "P0", status: "available", reason: "Synthetic measurement fixture. Not live data.", methodology: sourceId === "ramp" ? "Monthly median, top-10% and top-1% AI spend per employee from Ramp card and bill-pay transactions." : "Matched complete-period observations with a frozen cohort.", tickers: sourceId === "ramp" ? ["MSFT", "AMZN", "GOOGL", "META", "ORCL"] : ["NVDA"], source_ids: [sourceId],
    metrics: [{
      id: sourceId === "ramp" ? "spend.top_1_percent_median_pepm" : `${id}-series`,
      label: sourceId === "ramp" ? "Top 1% firm median AI spend per employee" : title,
      value: sourceId === "ramp" ? 7205.13 : 42,
      unit,
      period_start: sourceId === "ramp" ? "2026-08-01" : "2026-09-06",
      period_end: sourceId === "ramp" ? "2026-08-31" : "2026-09-06",
      published_at: sourceId === "ramp" ? "2026-09-09T00:00:00Z" : null,
      fetched_at: "2026-09-07T12:00:00Z",
      source_id: sourceId,
      source_url: sourceId === "ramp" ? "https://ramp.com/data/ai-index" : "https://example.com/evidence",
      measurement: "observed",
      methodology_version: sourceId === "ramp" ? "ramp-spend-intensity-v2-jun2026" : "1",
      cohort_version: sourceId === "ramp" ? "ramp-us-business-panel-70k-v2" : "fixture-v1",
      lineage_group: sourceId === "ramp" ? "ramp" : "fixture-host",
      raw_hash: "a".repeat(64),
      metadata: sourceId === "ramp" ? { measurement_limits: ["Undercounts free AI tools and personal employee accounts", "Top 1% cohort is small and revised as late transactions arrive"] } : { coverage_numerator: 4, coverage_denominator: 5, exclusions: ["Incomplete periods"] },
    }],
    history: Array.from({ length: 6 }, (_, i) => ({
      date: sourceId === "ramp" ? `2026-0${Math.min(i + 3, 8)}-01` : `2026-09-0${i + 1}`,
      value: sourceId === "ramp" ? 5000 + i * 400 : 30 + i * 2 + (i % 2 ? 4 : 0),
      unit,
      series_id: sourceId === "ramp" ? "spend.top_1_percent_median_pepm" : `${id}-series`,
      label: sourceId === "ramp" ? "Top 1% firm median AI spend per employee" : title,
      source_id: sourceId,
    })),
  })),
};
