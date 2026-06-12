import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import {
  HOST_METRICS_WINDOW_MS,
  type HostMetricsPayload,
  type HostMetricsRow,
} from "@/lib/adminHostMetrics";

// Serves the last hour of host_metrics samples (migration 0012, DUR-12) for
// the admin host-metrics strip: CPU / memory / event-loop-lag latest + trend.
// Bounded query riding idx_host_metrics_taken_at. Never static-cached
// (cache contract).
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// One row per minute -> ~60 rows per window; 2x headroom for clock drift
// and duplicate timer firings.
const MAX_SAMPLE_ROWS = 120;

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const since = new Date(Date.now() - HOST_METRICS_WINDOW_MS).toISOString();
  try {
    const db = getDb();
    const result = await db.execute({
      sql: `SELECT taken_at, cpu_pct, mem_used_mb, mem_avail_mb, load1,
                   swap_used_mb, loop_lag_ms, units_json
            FROM host_metrics
            WHERE taken_at >= ?
            ORDER BY taken_at ASC
            LIMIT ?`,
      args: [since, MAX_SAMPLE_ROWS],
    });

    const rows: HostMetricsRow[] = result.rows.map((row) => ({
      taken_at: String(row.taken_at),
      cpu_pct: row.cpu_pct == null ? null : Number(row.cpu_pct),
      mem_used_mb: row.mem_used_mb == null ? null : Number(row.mem_used_mb),
      mem_avail_mb: row.mem_avail_mb == null ? null : Number(row.mem_avail_mb),
      load1: row.load1 == null ? null : Number(row.load1),
      swap_used_mb: row.swap_used_mb == null ? null : Number(row.swap_used_mb),
      loop_lag_ms: row.loop_lag_ms == null ? null : Number(row.loop_lag_ms),
      units_json: row.units_json == null ? null : String(row.units_json),
    }));

    const payload: HostMetricsPayload = {
      window_ms: HOST_METRICS_WINDOW_MS,
      since,
      rows,
    };
    return setNoStoreResponseHeaders(NextResponse.json(payload), requestId);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    // Pre-migration (or laptop without Turso creds): an empty series is a
    // legitimate pending state — 200 + flag, never 4xx console noise.
    const payload: HostMetricsPayload = {
      window_ms: HOST_METRICS_WINDOW_MS,
      since,
      rows: [],
      missing: true,
    };
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...payload, error: detail }),
      requestId,
    );
  }
}
