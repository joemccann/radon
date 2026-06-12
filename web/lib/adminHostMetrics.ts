/**
 * Pure shaping helpers for the /admin host-metrics strip (DUR-12).
 *
 * Rows come from the Turso `host_metrics` table (migration 0012), written
 * every minute by scripts/host_metrics_sampler.py on the VPS. Everything
 * here is dependency-free + DOM-free so it is unit-testable; the route
 * (/api/admin/host-metrics) does the bounded query, the strip component
 * renders what these helpers derive.
 */

export type HostMetricsRow = {
  taken_at: string;
  cpu_pct: number | null;
  mem_used_mb: number | null;
  mem_avail_mb: number | null;
  load1: number | null;
  swap_used_mb: number | null;
  loop_lag_ms: number | null;
  units_json?: string | null;
};

/** Shape served by GET /api/admin/host-metrics. `missing` means the table
 * hasn't been migrated yet (200 + flag, never a 4xx). */
export type HostMetricsPayload = {
  window_ms: number;
  since: string;
  rows: HostMetricsRow[];
  missing?: boolean;
};

/** Trend window the route queries — 1h of minutely samples. */
export const HOST_METRICS_WINDOW_MS = 3_600_000;

/** The sampler fires every minute; >5 min of silence means it is dead or
 * the deploy hasn't landed yet (matches half the 10-min freshness window
 * in serviceHealthWindows.ts). */
export const HOST_METRICS_STALE_AFTER_MS = 5 * 60_000;

export type HostUnitSnapshot = {
  unit: string;
  active_state: string;
  n_restarts: number;
};

export type HostMetricsSummary = {
  latest: HostMetricsRow | null;
  /** True when no sample exists inside the staleness window. */
  stale: boolean;
  /** Ascending-by-time series for sparklines; null samples dropped. */
  cpuTrend: number[];
  memUsedTrend: number[];
  loopLagTrend: number[];
  /** radon-* units in ActiveState=failed on the latest sample. */
  failedUnits: string[];
  /** Sum of NRestarts across units on the latest sample; null when the
   * snapshot is absent or unparseable. */
  totalRestarts: number | null;
};

export type HostMetricTone = "positive" | "warning" | "negative" | "neutral";

// 2 vCPU box: sustained >70% is worth a look, >90% is the wedge regime.
const CPU_WARNING_AT = 70;
const CPU_NEGATIVE_AT = 90;
// 7.6 GB total, 0 swap: <1 GB available is tight, <512 MB is OOM territory.
const MEM_AVAIL_WARNING_BELOW_MB = 1024;
const MEM_AVAIL_NEGATIVE_BELOW_MB = 512;
// Healthy loop turns the roundtrip in microseconds; 100ms means real
// blocking work on the loop, 500ms is the documented wedge signature.
const LOOP_LAG_WARNING_AT_MS = 100;
const LOOP_LAG_NEGATIVE_AT_MS = 500;

function parseUnits(unitsJson: string | null | undefined): HostUnitSnapshot[] | null {
  if (!unitsJson) return null;
  try {
    const parsed: unknown = JSON.parse(unitsJson);
    if (!Array.isArray(parsed)) return null;
    return parsed
      .filter((u): u is Record<string, unknown> => typeof u === "object" && u !== null)
      .map((u) => ({
        unit: String(u.unit ?? ""),
        active_state: String(u.active_state ?? "unknown"),
        n_restarts: typeof u.n_restarts === "number" ? u.n_restarts : 0,
      }))
      .filter((u) => u.unit.length > 0);
  } catch {
    return null;
  }
}

function trendOf(
  sorted: HostMetricsRow[],
  pick: (r: HostMetricsRow) => number | null,
): number[] {
  return sorted
    .map(pick)
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
}

export function summarizeHostMetrics(
  rows: HostMetricsRow[],
  nowMs: number = Date.now(),
): HostMetricsSummary {
  const sorted = [...rows].sort(
    (a, b) => Date.parse(a.taken_at) - Date.parse(b.taken_at),
  );
  const latest = sorted.length > 0 ? sorted[sorted.length - 1] : null;
  const latestAtMs = latest ? Date.parse(latest.taken_at) : NaN;
  const stale =
    !latest || Number.isNaN(latestAtMs) || nowMs - latestAtMs > HOST_METRICS_STALE_AFTER_MS;

  const units = parseUnits(latest?.units_json);
  return {
    latest,
    stale,
    cpuTrend: trendOf(sorted, (r) => r.cpu_pct),
    memUsedTrend: trendOf(sorted, (r) => r.mem_used_mb),
    loopLagTrend: trendOf(sorted, (r) => r.loop_lag_ms),
    failedUnits: units?.filter((u) => u.active_state === "failed").map((u) => u.unit) ?? [],
    totalRestarts: units ? units.reduce((sum, u) => sum + u.n_restarts, 0) : null,
  };
}

export function cpuTone(cpuPct: number | null): HostMetricTone {
  if (cpuPct === null) return "neutral";
  if (cpuPct >= CPU_NEGATIVE_AT) return "negative";
  if (cpuPct >= CPU_WARNING_AT) return "warning";
  return "positive";
}

export function memAvailTone(memAvailMb: number | null): HostMetricTone {
  if (memAvailMb === null) return "neutral";
  if (memAvailMb < MEM_AVAIL_NEGATIVE_BELOW_MB) return "negative";
  if (memAvailMb < MEM_AVAIL_WARNING_BELOW_MB) return "warning";
  return "positive";
}

export function loopLagTone(loopLagMs: number | null): HostMetricTone {
  if (loopLagMs === null) return "neutral";
  if (loopLagMs >= LOOP_LAG_NEGATIVE_AT_MS) return "negative";
  if (loopLagMs >= LOOP_LAG_WARNING_AT_MS) return "warning";
  return "positive";
}
