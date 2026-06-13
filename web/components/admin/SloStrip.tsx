"use client";

import { summarizeSlos, type SloPayload, type SloSummary } from "@/lib/adminSlo";

type Tone = "positive" | "warning" | "negative" | "neutral";

/**
 * The "SLO 7d" strip (DUR-16): attainment vs target for the three
 * contract SLOs — edge reachability 99.5%, RTH tick freshness 99%, scan
 * freshness 95% — computed from the Tier-3 prober's external_probe_runs
 * history. Honest like its siblings: a missing table or a column with no
 * applicable samples renders "--", never a fabricated 100%.
 */
export default function SloStrip({ slo }: { slo: SloPayload | null }) {
  const usable = slo && !slo.missing && Array.isArray(slo.rows) && slo.rows.length > 0;
  const summaries = usable ? summarizeSlos(slo.rows) : null;
  const windowDays = Math.round((slo?.window_ms ?? 7 * 86_400_000) / 86_400_000) || 7;

  return (
    <section className="admin-reliability-strip" data-testid="slo-strip">
      {(summaries ?? summarizeSlos([])).map((summary) => (
        <SloTile key={summary.key} summary={summary} windowDays={windowDays} pending={!usable} />
      ))}
    </section>
  );
}

function SloTile({
  summary,
  windowDays,
  pending,
}: {
  summary: SloSummary;
  windowDays: number;
  pending: boolean;
}) {
  const label = `${summary.label} (${windowDays}d)`;
  const tone: Tone =
    summary.met === null ? "neutral" : summary.met ? "positive" : "negative";
  const value =
    summary.attainmentPct === null ? "--" : `${summary.attainmentPct.toFixed(2)}%`;
  const sub = pending
    ? "probe history pending"
    : summary.attainmentPct === null
      ? `target ${summary.targetPct}% · no applicable runs`
      : `target ${summary.targetPct}% · ${summary.samples} runs`;

  return (
    <div
      className={`admin-tile admin-tile-${tone}`}
      data-testid={`tile-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-+$/, "")}`}
    >
      <div className="admin-tile-label">{label}</div>
      <div className="admin-tile-value">
        <span className={`admin-status-dot admin-status-dot-${tone}`} aria-hidden />
        {value}
      </div>
      <div className="admin-tile-sub">{sub}</div>
    </div>
  );
}
