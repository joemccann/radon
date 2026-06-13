/**
 * 7-day SLO attainment for the operator admin panel (DUR-16), computed
 * from the append-only `external_probe_runs` history (migration 0013 —
 * one row per Tier-3 off-box probe run).
 *
 * SLO targets are fixed by the DUR-16 contract:
 *
 *   edge reachability   99.5% / 7d   <- edge_ok
 *   RTH tick freshness  99%   / 7d   <- tick_fresh
 *   scan freshness      95%   / 7d   <- scan_fresh
 *
 * NULL columns mean "not applicable on that run" (quiet market, endpoint
 * pending deploy) and are excluded from the denominator — a quiet weekend
 * is never an SLO miss (feedback_service_health_staleness). Every number
 * is honest: no applicable samples renders null ("--"), never a fabricated
 * 100%. Dependency-free + DOM-free so it is unit-testable.
 */

/** One row of `external_probe_runs` as served by GET /api/admin/slo. */
export type ExternalProbeRunRow = {
  run_at: string;
  edge_ok: number | null;
  user_path_ok: number | null;
  freshness_ok: number | null;
  tick_fresh: number | null;
  scan_fresh: number | null;
  latency_ms: number | null;
};

export const SLO_WINDOW_MS = 7 * 24 * 3_600_000;

/** Shape served by GET /api/admin/slo. `missing` means the probe-history
 * table hasn't been migrated / populated yet (200 + flag, never a 4xx). */
export type SloPayload = {
  window_ms: number;
  since: string;
  rows: ExternalProbeRunRow[];
  missing?: boolean;
};

export type SloKey = "edge" | "tick" | "scan";

export type SloDefinition = {
  key: SloKey;
  label: string;
  targetPct: number;
  field: "edge_ok" | "tick_fresh" | "scan_fresh";
};

export const SLO_DEFINITIONS: readonly SloDefinition[] = [
  { key: "edge", label: "Edge reach", targetPct: 99.5, field: "edge_ok" },
  { key: "tick", label: "RTH ticks", targetPct: 99, field: "tick_fresh" },
  { key: "scan", label: "Scan fresh", targetPct: 95, field: "scan_fresh" },
];

export type SloSummary = {
  key: SloKey;
  label: string;
  targetPct: number;
  /** % of applicable runs that met the check; null when none were applicable. */
  attainmentPct: number | null;
  /** Applicable (non-NULL) runs in the window. */
  samples: number;
  /** attainment >= target; null when attainment is null. */
  met: boolean | null;
};

export function summarizeSlos(rows: ExternalProbeRunRow[]): SloSummary[] {
  return SLO_DEFINITIONS.map((definition) => {
    const applicable = rows.filter((row) => {
      const value = row[definition.field];
      return value === 0 || value === 1;
    });
    const okCount = applicable.filter((row) => row[definition.field] === 1).length;
    const attainmentPct = applicable.length > 0 ? (okCount / applicable.length) * 100 : null;
    return {
      key: definition.key,
      label: definition.label,
      targetPct: definition.targetPct,
      attainmentPct,
      samples: applicable.length,
      met: attainmentPct === null ? null : attainmentPct >= definition.targetPct,
    };
  });
}
