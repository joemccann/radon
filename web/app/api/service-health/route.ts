import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import {
  getMarketStateFromDate,
  getServiceCategory,
  isStale,
  type ServiceCategory,
} from "@/lib/serviceHealthWindows";
import { formatServiceHealthError } from "@/lib/serviceHealthError";
import { withTimeout } from "@/lib/asyncTimeout";

// Disable Next.js static caching: this handler reads live DB state.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const DB_READ_TIMEOUT_MS = 3_000;

export type ServiceHealthRow = {
  service: string;
  state: string;
  category: ServiceCategory;
  last_attempt_started_at: string | null;
  last_attempt_finished_at: string | null;
  /**
   * Original raw payload as written by the worker — JSON-encoded object
   * or a plain string. Kept on the wire for diagnostic clients; UIs
   * should prefer ``error_summary`` to avoid leaking JSON structure.
   */
  last_error: string | null;
  /**
   * Human-readable single-line summary of ``last_error`` produced by
   * ``formatServiceHealthError``. Always populated when ``last_error``
   * is non-null, even on parse failure (uses fallback copy). Safe to
   * concatenate directly into user-facing copy.
   */
  error_summary: string | null;
  updated_at: string;
};

function isFailingState(state: string): boolean {
  // ``ok``, ``syncing``, ``paused`` are healthy / informational.
  // ``dormant`` is informational too — an on-demand service that no
  // user has visited recently. Everything else — including ``stale``
  // from the freshness gate and ``error`` from a real exception —
  // should fire the degraded banner.
  if (state === "ok" || state === "syncing" || state === "paused" || state === "dormant") {
    return false;
  }
  return true;
}

/**
 * True when the row should fire the red degraded banner. ``error``
 * rows from any category and ``stale`` rows from scheduled writers
 * both qualify; on-demand ``dormant`` rows do not.
 */
function isDegradedRow(row: ServiceHealthRow): boolean {
  if (row.state === "error") return true;
  if (row.state === "stale" && row.category === "scheduled") return true;
  return false;
}

function isDormantRow(row: ServiceHealthRow): boolean {
  return row.state === "dormant";
}

/**
 * Coerce ``ok`` rows past their freshness window into ``stale`` for
 * scheduled writers and ``dormant`` for on-demand writers. Real non-ok
 * states (error, syncing, paused) are passed through untouched — the
 * gate only adds signal, never hides it.
 */
function applyStalenessGate(row: ServiceHealthRow, nowMs: number): ServiceHealthRow {
  if (row.state !== "ok") return row;
  const market = getMarketStateFromDate(new Date(nowMs));
  if (!isStale(row.service, row.updated_at, market, nowMs)) return row;
  const coerced = row.category === "on-demand" ? "dormant" : "stale";
  return { ...row, state: coerced };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  try {
    const db = getDb();
    const result = await withTimeout(
      db.execute({
        sql: `
        SELECT service, state, last_attempt_started_at, last_attempt_finished_at,
               last_error, updated_at
          FROM service_health
         ORDER BY updated_at DESC
      `,
        args: [],
      }),
      DB_READ_TIMEOUT_MS,
      `service_health read timed out after ${DB_READ_TIMEOUT_MS}ms`,
    );

    const nowMs = Date.now();
    const rows: ServiceHealthRow[] = [];
    for (const row of result.rows) {
      const r = row as unknown as Record<string, unknown>;
      const lastErrorRaw = (r.last_error ?? null) as string | null;
      const service = String(r.service ?? "");
      const raw: ServiceHealthRow = {
        service,
        state: String(r.state ?? "unknown"),
        category: getServiceCategory(service),
        last_attempt_started_at: (r.last_attempt_started_at ?? null) as string | null,
        last_attempt_finished_at: (r.last_attempt_finished_at ?? null) as string | null,
        last_error: lastErrorRaw,
        // Pre-normalize the human-readable summary at the API boundary
        // so every UI consumer (banner, future status pages, etc.) sees
        // a clean string instead of a JSON-stringified blob.
        error_summary: lastErrorRaw == null ? null : formatServiceHealthError(lastErrorRaw),
        updated_at: String(r.updated_at ?? ""),
      };
      // Coerce stale ``ok`` rows so the banner can see them. Per-service
      // freshness windows live in ``lib/serviceHealthWindows.ts`` and
      // expand off-hours for market-cadence services so a quiet weekend
      // doesn't fire a false alarm. The category drives whether the
      // coercion produces ``stale`` (scheduled — real problem) or
      // ``dormant`` (on-demand — nobody has looked at it).
      rows.push(applyStalenessGate(raw, nowMs));
    }

    const failing = rows.filter((r) => isFailingState(r.state));
    const degraded = rows.filter(isDegradedRow);
    const dormant = rows.filter(isDormantRow);

    const response = NextResponse.json({
      services: rows,
      failing,
      degraded_count: degraded.length,
      dormant_count: dormant.length,
      summary: {
        total: rows.length,
        failing_count: failing.length,
      },
    });
    return setNoStoreResponseHeaders(response, requestId);
  } catch (error) {
    // DB unreachable — return a synthetic degraded provider row instead of
    // hanging or shipping an empty healthy-looking service list.
    const message = error instanceof Error ? error.message : "service_health read failed";
    console.warn(`[service-health] ${message}`);
    const updatedAt = new Date().toISOString();
    const dbRow: ServiceHealthRow = {
      service: "turso-db",
      state: "error",
      category: "scheduled",
      last_attempt_started_at: null,
      last_attempt_finished_at: null,
      last_error: JSON.stringify({ message }),
      error_summary: formatServiceHealthError(message),
      updated_at: updatedAt,
    };
    const response = NextResponse.json({
      services: [dbRow],
      failing: [dbRow],
      degraded_count: 1,
      dormant_count: 0,
      summary: { total: 1, failing_count: 1 },
      warning: message,
    });
    return setNoStoreResponseHeaders(response, requestId);
  }
}
