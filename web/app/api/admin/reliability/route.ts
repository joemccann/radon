import { requireRouteAccess } from "@/lib/routeAccess";
import { NextResponse } from "next/server";
import { cachedRead } from "@/lib/dbCache";
import { dbExecute } from "@/lib/dbExecute";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import {
  RELIABILITY_WINDOW_MS,
  type ReliabilityHistoryPayload,
  type ServiceHealthEventRow,
} from "@/lib/adminReliability";
import {
  filterApplicableServiceHealthBaseline,
  filterApplicableServiceHealthRows,
  replicaDatabaseExists,
} from "@/lib/serviceHealthApplicability";

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
// Transition events accrue slowly; a short TTL coalesces the admin
// workspace's polling into one Turso read per window
// (contract: tests/db-read-cache-contract.test.ts).
const READ_CACHE_TTL_MS = 10_000;

async function readReliabilityPayload(): Promise<ReliabilityHistoryPayload> {
  const since = new Date(Date.now() - RELIABILITY_WINDOW_MS).toISOString();
  const eventsResult = await dbExecute({
    sql: `SELECT id, service, state, detail, created_at
          FROM (
            SELECT id, service, state, detail, created_at
            FROM service_health_events
            WHERE created_at >= ?
            ORDER BY created_at DESC
            LIMIT ?
          ) newest
          ORDER BY created_at ASC`,
    args: [since, MAX_EVENT_ROWS + 1],
  }, { label: "admin-reliability-events" });
  // SQLite/libSQL bare-column-with-MAX semantics: state comes from the row
  // holding MAX(created_at) per service — the state at window start.
  const baselineResult = await dbExecute({
    sql: `SELECT service, state, MAX(created_at) AS created_at
          FROM service_health_events
          WHERE created_at < ?
          GROUP BY service`,
    args: [since],
  }, { label: "admin-reliability-baseline" });

  const truncated = eventsResult.rows.length > MAX_EVENT_ROWS;
  const events: ServiceHealthEventRow[] = eventsResult.rows.slice(-MAX_EVENT_ROWS).map((row) => ({
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

  return { window_ms: RELIABILITY_WINDOW_MS, since, events, baseline, truncated };
}

export const radonCapability = "admin";

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { operatorOnly: true });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const cachedPayload = await cachedRead(
      "admin:reliability",
      READ_CACHE_TTL_MS,
      readReliabilityPayload,
    );
    // Apply live subsystem applicability after the DB cache. Latest-state and
    // transition rows persist after retirement, while disk state can change
    // during the cache window.
    const replicaPresent = replicaDatabaseExists();
    const payload: ReliabilityHistoryPayload = {
      ...cachedPayload,
      events: filterApplicableServiceHealthRows(cachedPayload.events, replicaPresent),
      baseline: filterApplicableServiceHealthBaseline(cachedPayload.baseline, replicaPresent),
    };
    return setNoStoreResponseHeaders(NextResponse.json(payload), requestId);
  } catch {
    // Pre-migration (or laptop without Turso creds): an empty history is a
    // legitimate pending state — 200 + flag, never 4xx console noise.
    const payload: ReliabilityHistoryPayload = {
      window_ms: RELIABILITY_WINDOW_MS,
      since: new Date(Date.now() - RELIABILITY_WINDOW_MS).toISOString(),
      events: [],
      baseline: {},
      missing: true,
    };
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...payload, error: "Reliability history unavailable" }),
      requestId,
    );
  }
}
