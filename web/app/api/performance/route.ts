import { requireRouteAccess } from "@/lib/routeAccess";
import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { isPerformanceBehindPortfolioSync } from "@/lib/performanceFreshness";

import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { dbExecute } from "@/lib/dbExecute";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getMarketStateFromDate } from "@/lib/serviceHealthWindows";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const PERFORMANCE_PATH = join(process.cwd(), "..", "data", "performance.json");

// §4.4 — TTL is market-state aware: 5 min when OPEN, 60 min otherwise.
// CLOSED covers extended + overnight + weekends (getMarketStateFromDate
// returns "closed" | "extended" | "open").
const CACHE_TTL_OPEN_MS = 5 * 60_000;
const CACHE_TTL_CLOSED_MS = 60 * 60_000;

function getCacheTtlMs(now: Date = new Date()): number {
  const state = getMarketStateFromDate(now);
  return state === "open" ? CACHE_TTL_OPEN_MS : CACHE_TTL_CLOSED_MS;
}

async function readJsonFile(path: string): Promise<Record<string, unknown> | null> {
  try {
    const raw = await readFile(path, "utf-8");
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function extractTimestampValue(data: Record<string, unknown> | null, key: string): string | null {
  const value = data?.[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

/** Payload v2 states its freshness as `generated_at` / `nav_as_of`; the legacy
 *  writer stated it as `last_sync` / `as_of`. Hand both shapes over so the
 *  comparison is never left reading two nulls and answering "behind". */
function isCacheBehindPortfolio(
  performance: Record<string, unknown> | null,
  portfolio: Record<string, unknown> | null,
): boolean {
  const portfolioLastSync = extractTimestampValue(portfolio, "last_sync");
  return isPerformanceBehindPortfolioSync(
    performance
      ? {
          last_sync: extractTimestampValue(performance, "last_sync"),
          as_of: extractTimestampValue(performance, "as_of"),
          generated_at: extractTimestampValue(performance, "generated_at"),
          nav_as_of: extractTimestampValue(performance, "nav_as_of"),
        }
      : null,
    portfolioLastSync,
  );
}

/**
 * Hard floor between background rebuilds, deliberately independent of WHY one
 * was judged necessary. A rebuild runs a full Flex SendRequest plus polls
 * against a single query id, and IBKR answers a request storm with a 24h to
 * 168h throttle embargo — this repo has already taken one. FastAPI's own
 * `_running_build` guard only dedupes CONCURRENT builds, so sequential GETs
 * need the floor on the caller side, where a freshness bug can no longer
 * reopen the loop.
 */
const MIN_REBUILD_INTERVAL_MS = 5 * 60_000;

let lastBackgroundRebuildAtMs = 0;

/**
 * Freshness comes from the payload's own full-ISO `generated_at` (payload v2),
 * falling back to the legacy `last_sync` and only then to the row key. Two
 * writers wrote `taken_at` in two shapes; a date-only key reads as ~midnight
 * and makes every row look permanently stale, which is what kept this route
 * rebuilding against a five-month-old snapshot.
 */
function payloadTimestampMs(
  payload: Record<string, unknown>,
  rowTakenAt: string,
): number | null {
  return (
    contentTimestampMs(payload.generated_at)
    ?? contentTimestampMs(payload.last_sync)
    ?? contentTimestampMs(rowTakenAt)
  );
}

/** Phase 2.3 — latest Turso snapshot, timestamped by the payload's own instant. */
async function readPerformanceFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const result = await dbExecute({
    sql: `SELECT taken_at, payload FROM performance_snapshots ORDER BY taken_at DESC LIMIT 1`,
    args: [],
  }, { label: "performance" });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { taken_at: string; payload: string };
  const data = JSON.parse(row.payload) as Record<string, unknown>;
  return { data, timestampMs: payloadTimestampMs(data, row.taken_at) };
}

/**
 * §C.6 — missing data is a status, never an HTTP fault. The UI branches on
 * `status` and renders the warning; it must never see a 5xx envelope it has to
 * guess at, and the upstream error text never reaches the client.
 */
function navUnavailablePayload(): Record<string, unknown> {
  return {
    schema_version: 2,
    status: "unavailable",
    generated_at: new Date().toISOString(),
    nav_source: "none",
    nav_as_of: "",
    nav_sessions_behind: 0,
    flows_status: "failed",
    counts: { n_nav_observations: 0, n_subperiods: 0, n_returns: 0, n_skipped: 0, n_suspect: 0 },
    benchmark: null,
    series: [],
    subperiods: [],
    warnings: [
      {
        code: "NAV_UNAVAILABLE",
        severity: "error",
        message: "No NAV series available (Flex, disk and Turso all empty).",
        context: {},
      },
    ],
  };
}

async function readPortfolioFromDb(): Promise<Record<string, unknown> | null> {
  try {
    const result = await dbExecute({
      sql: `SELECT payload FROM portfolio_snapshots ORDER BY taken_at DESC LIMIT 1`,
      args: [],
    }, { label: "performance-portfolio" });
    if (result.rows.length === 0) return null;
    const row = result.rows[0] as unknown as { payload?: unknown };
    if (typeof row.payload !== "string") return null;
    return JSON.parse(row.payload) as Record<string, unknown>;
  } catch {
    return null;
  }
}

async function readPerformanceFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const data = await readJsonFile(PERFORMANCE_PATH);
  if (!data) return null;
  return { data, timestampMs: payloadTimestampMs(data, "") };
}

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "performance:route", limit: 20, windowMs: 60_000 } });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const cacheTtlMs = getCacheTtlMs();
  const [perfRead, portfolioSnapshot] = await Promise.all([
    // Fresher of DB row and disk JSON — a frozen writer on either side
    // never wins. The downstream freshness logic uses whichever was served.
    // TTL is market-state aware (§4.4): 5 min OPEN, 60 min CLOSED.
    dbFirstRead({
      fromDb: readPerformanceFromDb,
      fromDisk: readPerformanceFromDisk,
      maxAgeMs: cacheTtlMs,
      label: "performance",
    }),
    readPortfolioFromDb(),
  ]);
  const cachedPerformance = perfRead.ok ? perfRead.data : null;

  // §4.4: removed 35s POST /portfolio/sync block — portfolio sync is
  // independent (WorkspaceShell handles it). No blocking sync here.

  // Honest methodology (§4.4) is owned by the TWR builder
  // (curve_type: twr_modified_dietz_daily, return_basis: time_weighted,
  //  risk_free_rate: FRED DGS3MO, library_strategy: fred_dgs3mo).
  // The route passes payload through unchanged — no rewriting.
  // insufficient_data payloads are returned 200 with warnings so the
  // UI can render the measurement description empty state.

  const stale = perfRead.ok ? !perfRead.fresh : true;
  const behindPortfolio = isCacheBehindPortfolio(cachedPerformance, portfolioSnapshot);

  // The route used to compute both of these and then DISCARD them: the
  // `!shouldRebuild` branch and the `cachedPerformance` branch returned the
  // byte-identical response, and `triggerBackgroundRebuild` was an empty
  // function. A payload three days past its 60-minute CLOSED TTL was served
  // with no stale flag, no header and nothing to trigger a refresh — and the
  // payload's own honesty markers do not cover it, because
  // `nav_sessions_behind` and every NAV_STALE warning are frozen at BUILD
  // time, so a payload built Friday still reads ok / 0 on Monday. R-346.
  if (cachedPerformance) {
    const degraded = stale || behindPortfolio;
    const body = degraded
      ? { ...cachedPerformance, stale: true as const, stale_reason: stale ? "ttl" : "behind_portfolio" }
      : cachedPerformance;
    const response = setNoStoreResponseHeaders(NextResponse.json(body), requestId);
    if (degraded) response.headers.set("X-Radon-Stale", "1");
    return response;
  }

  return setNoStoreResponseHeaders(NextResponse.json(navUnavailablePayload()), requestId);
}

export async function POST(): Promise<Response> {
  const access = await requireRouteAccess(undefined, {
    operatorOnly: true,
    rate: { key: "performance-build", limit: 2, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  return setNoStoreResponseHeaders(
    NextResponse.json(
      { error: "Performance rebuild is file-ingest only. Use --from-file." },
      { status: 404 },
    ),
    requestId,
  );
}
