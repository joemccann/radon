import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import {
  SLO_WINDOW_MS,
  type ExternalProbeRunRow,
  type SloPayload,
} from "@/lib/adminSlo";

// Serves the 7-day external_probe_runs history (migration 0013, DUR-16) for
// the admin SLO strip: edge-reachability / RTH-tick / scan-freshness
// attainment vs target. Bounded query riding idx_external_probe_runs_run_at.
// Follows the /api/admin/reliability + /api/admin/host-metrics pattern.
// Never static-cached (cache contract).
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// The prober runs every few minutes from GitHub Actions cron: a 5-minute
// cadence is ~2016 rows per 7-day window; 5000 caps a pathological
// double-scheduled workflow rather than shipping it to the client.
const MAX_RUN_ROWS = 5000;

function numberOrNull(value: unknown): number | null {
  return value == null ? null : Number(value);
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const since = new Date(Date.now() - SLO_WINDOW_MS).toISOString();
  try {
    const db = getDb();
    const result = await db.execute({
      sql: `SELECT run_at, edge_ok, user_path_ok, freshness_ok,
                   tick_fresh, scan_fresh, latency_ms
            FROM external_probe_runs
            WHERE run_at >= ?
            ORDER BY run_at ASC
            LIMIT ?`,
      args: [since, MAX_RUN_ROWS],
    });

    const rows: ExternalProbeRunRow[] = result.rows.map((row) => ({
      run_at: String(row.run_at),
      edge_ok: numberOrNull(row.edge_ok),
      user_path_ok: numberOrNull(row.user_path_ok),
      freshness_ok: numberOrNull(row.freshness_ok),
      tick_fresh: numberOrNull(row.tick_fresh),
      scan_fresh: numberOrNull(row.scan_fresh),
      latency_ms: numberOrNull(row.latency_ms),
    }));

    const payload: SloPayload = { window_ms: SLO_WINDOW_MS, since, rows };
    return setNoStoreResponseHeaders(NextResponse.json(payload), requestId);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    // Pre-migration (or laptop without Turso creds): an empty history is a
    // legitimate pending state — 200 + flag, never 4xx console noise.
    const payload: SloPayload = {
      window_ms: SLO_WINDOW_MS,
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
