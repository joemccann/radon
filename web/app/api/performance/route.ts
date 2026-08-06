import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { isPerformanceBehindPortfolioSync } from "@/lib/performanceFreshness";
import { radonFetch } from "@/lib/radonApi";
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
        }
      : null,
    portfolioLastSync,
  );
}

/**
 * Fire-and-forget background rebuild trigger.
 * 5s timeout, swallow all errors — caller already returned cached data.
 * §4.4: keep SWR — serve stale immediately, rebuild in background.
 */
function triggerBackgroundRebuild(): void {
  radonFetch("/performance/background", { method: "POST", timeout: 5_000 }).catch(() => {});
}

/** Phase 2.3 — latest Turso snapshot, timestamped by the taken_at row key. */
async function readPerformanceFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const result = await dbExecute({
    sql: `SELECT taken_at, payload FROM performance_snapshots ORDER BY taken_at DESC LIMIT 1`,
    args: [],
  }, { label: "performance" });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { taken_at: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.taken_at),
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
  return { data, timestampMs: contentTimestampMs(data.last_sync) };
}

export async function GET(): Promise<Response> {
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

  // §4.4 shouldRebuild: TTL-gated staleness OR portfolio freshness lag.
  // Covers both "served snapshot is past its market-state window" and
  // "performance last_sync/as_of lags portfolio last_sync" (twr_subperiods
  // vs nav_snapshots check is inside the builder; the route gates on
  // isPerformanceBehindPortfolioSync).
  const shouldRebuild = !cachedPerformance || stale || behindPortfolio;

  if (!shouldRebuild && cachedPerformance) {
    return setNoStoreResponseHeaders(NextResponse.json(cachedPerformance), requestId);
  }

  // insufficient_data is still a valid 200 — surface warnings, SWR in
  // background so a Flex backfill can fill the gap without blocking.
  if (cachedPerformance) {
    triggerBackgroundRebuild();
    return setNoStoreResponseHeaders(NextResponse.json(cachedPerformance), requestId);
  }

  // Cold start: no cache at all — must block on full TWR builder
  try {
    const data = await radonFetch("/performance", { method: "POST", timeout: 180_000 });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to generate performance metrics";
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status: 502 }),
      requestId,
    );
  }
}

export async function POST(): Promise<Response> {
  const requestId = getRequestId();
  try {
    const data = await radonFetch("/performance", { method: "POST", timeout: 190_000 });
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to generate performance metrics";
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status: 502 }),
      requestId,
    );
  }
}
