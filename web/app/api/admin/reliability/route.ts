import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import {
  RELIABILITY_WINDOW_MS,
  type ReliabilityHistoryPayload,
  type ServiceHealthEventRow,
} from "@/lib/adminReliability";

// Serves the append-only service_health_events history (migration 0011) for
// the admin Reliability Strip's time-series tiles: bounded 7-day window of
// transition events + a per-service baseline (last state before the window)
// so uptime is scored over the full window, not just from the first event.
// Both queries ride idx_service_health_events_service_created. Never
// static-cached (cache contract).
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// Hard row bound: ~30 writers transitioning hourly for a week is <5000; a
// pathological flap storm gets truncated rather than shipped to the client.
const MAX_EVENT_ROWS = 5000;

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const since = new Date(Date.now() - RELIABILITY_WINDOW_MS).toISOString();
  try {
    const db = getDb();
    const eventsResult = await db.execute({
      sql: `SELECT id, service, state, detail, created_at
            FROM service_health_events
            WHERE created_at >= ?
            ORDER BY created_at ASC
            LIMIT ?`,
      args: [since, MAX_EVENT_ROWS],
    });
    // SQLite/libSQL bare-column-with-MAX semantics: state comes from the row
    // holding MAX(created_at) per service — the state at window start.
    const baselineResult = await db.execute({
      sql: `SELECT service, state, MAX(created_at) AS created_at
            FROM service_health_events
            WHERE created_at < ?
            GROUP BY service`,
      args: [since],
    });

    const events: ServiceHealthEventRow[] = eventsResult.rows.map((row) => ({
      id: Number(row.id),
      service: String(row.service),
      state: String(row.state),
      detail: row.detail == null ? null : String(row.detail),
      created_at: String(row.created_at),
    }));
    const baseline: Record<string, string> = {};
    for (const row of baselineResult.rows) {
      baseline[String(row.service)] = String(row.state);
    }

    const payload: ReliabilityHistoryPayload = {
      window_ms: RELIABILITY_WINDOW_MS,
      since,
      events,
      baseline,
    };
    return setNoStoreResponseHeaders(NextResponse.json(payload), requestId);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    // Pre-migration (or laptop without Turso creds): an empty history is a
    // legitimate pending state — 200 + flag, never 4xx console noise.
    const payload: ReliabilityHistoryPayload = {
      window_ms: RELIABILITY_WINDOW_MS,
      since,
      events: [],
      baseline: {},
      missing: true,
    };
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...payload, error: detail }),
      requestId,
    );
  }
}
