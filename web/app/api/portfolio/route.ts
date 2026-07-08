import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { resetDb } from "@/lib/db";
import { dbExecute } from "@/lib/dbExecute";
import { cachedRead } from "@/lib/dbCache";
import { buildContractEntryDates, type JournalEntryRow } from "@/lib/entryDates";

// Disable Next.js static caching: this handler reads live Turso state.
// Without this, the framework freezes the first response and serves stale
// data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_TTL_MS = 60_000; // 1 minute
const DB_READ_TIMEOUT_MS = 3_000;
const LIVE_SYNC_TIMEOUT_MS = 35_000;
const LIVE_FALLBACK_CACHE_TTL_MS = 30_000;
// Server-side coalescing of the polled GET reads. The snapshot is the hot one;
// staleWhileError serves the last good portfolio through a brief Turso stall
// (longer outages exceed maxStale and fall through to the live-IB fallback).
// Trade-log dates are day-granularity, so a longer TTL is invisible.
const SNAPSHOT_CACHE_TTL_MS = 3_000;
const SNAPSHOT_MAX_STALE_MS = 60_000;
const TRADE_LOG_DATES_CACHE_TTL_MS = 10_000;

type PortfolioSnapshot = {
  data: Record<string, unknown>;
  takenAt: string;
  timestampMs: number | null;
};

function parseTimestampMs(value: unknown): number | null {
  if (typeof value !== "string" || value.length === 0) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

/** Read the latest portfolio snapshot from Turso. */
async function readPortfolioFromDb(): Promise<PortfolioSnapshot | null> {
  const result = await dbExecute({
    sql: `SELECT taken_at, payload FROM portfolio_snapshots ORDER BY taken_at DESC LIMIT 1`,
    args: [],
  }, { label: "portfolio snapshot", timeoutMs: DB_READ_TIMEOUT_MS });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { taken_at?: string; payload?: string };
  if (typeof row.payload !== "string") return null;
  const data = JSON.parse(row.payload) as Record<string, unknown>;
  const takenAt = typeof row.taken_at === "string" ? row.taken_at : "";
  return {
    data,
    takenAt,
    timestampMs: parseTimestampMs(data.last_sync) ?? parseTimestampMs(takenAt),
  };
}

function isPortfolioSnapshotStale(snapshot: PortfolioSnapshot | null): boolean {
  if (!snapshot?.timestampMs) {
    return true;
  }
  return Date.now() - snapshot.timestampMs > CACHE_TTL_MS;
}

function snapshotFromLiveSyncData(data: unknown): PortfolioSnapshot {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("Live portfolio sync returned invalid payload");
  }
  const portfolio = data as Record<string, unknown>;
  const takenAt = typeof portfolio.last_sync === "string" ? portfolio.last_sync : new Date().toISOString();
  return {
    data: portfolio,
    takenAt,
    timestampMs: parseTimestampMs(portfolio.last_sync) ?? Date.now(),
  };
}

/** Load ticker → latest trade date.
 *
 * The canonical source is the `journal` table. Missing rows or query
 * failures return an empty map; this route never falls back to
 * the old flat trade-log mirror.
 */
async function loadTradeLogDates(): Promise<Record<string, string>> {
  try {
    const result = await dbExecute({
      sql: `
        SELECT
          json_extract(payload, '$.ticker') AS ticker,
          MAX(COALESCE(filled_at, json_extract(payload, '$.date'))) AS date
        FROM journal
        WHERE json_extract(payload, '$.ticker') IS NOT NULL
        GROUP BY json_extract(payload, '$.ticker')
      `,
      args: [],
    }, { label: "portfolio trade-log date", timeoutMs: DB_READ_TIMEOUT_MS });
    if (result.rows.length > 0) {
      const dates: Record<string, string> = {};
      for (const row of result.rows) {
        const r = row as unknown as { ticker?: string; date?: string };
        if (typeof r.ticker === "string" && typeof r.date === "string") {
          dates[r.ticker] = r.date;
        }
      }
      return dates;
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.warn(`[Portfolio] trade_log_dates journal query failed: ${message}`);
  }
  return {};
}

/** Load per-contract opening-episode date.
 *
 * Keyed `SYMBOL|EXPIRY|RIGHT|STRIKE` (the conId-free journal form the share
 * card reconstructs). `buildContractEntryDates` walks each contract's fills by
 * signed net quantity and returns the date its current open episode began — the
 * position's true entry day even once it is fully closed and gone from the
 * portfolio, which the per-ticker trade_log_dates MAX cannot give. Correct
 * across re-opens, duplicated session/Flex rows, and out-of-order fills.
 */
async function loadContractOpenDates(): Promise<Record<string, string>> {
  try {
    const result = await dbExecute({
      sql: `
        SELECT
          json_extract(payload, '$.ticker')    AS ticker,
          json_extract(payload, '$.expiry')    AS expiry,
          json_extract(payload, '$.right')     AS opt_right,
          json_extract(payload, '$.strike')    AS strike,
          json_extract(payload, '$.action')    AS action,
          json_extract(payload, '$.contracts') AS contracts,
          COALESCE(filled_at, json_extract(payload, '$.date')) AS date
        FROM journal
        WHERE json_extract(payload, '$.right')  IS NOT NULL
          AND json_extract(payload, '$.strike') IS NOT NULL
      `,
      args: [],
    }, { label: "portfolio contract open date", timeoutMs: DB_READ_TIMEOUT_MS });
    const rows = result.rows as unknown as JournalEntryRow[];
    return buildContractEntryDates(rows);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.warn(`[Portfolio] contract_open_dates journal query failed: ${message}`);
    return {};
  }
}

/** The two journal-derived entry-date maps the share card consults, read
 *  concurrently and cached independently. */
async function loadPortfolioEntryDates(): Promise<{
  trade_log_dates: Record<string, string>;
  contract_open_dates: Record<string, string>;
}> {
  const [trade_log_dates, contract_open_dates] = await Promise.all([
    cachedRead("portfolio:tradeLogDates", TRADE_LOG_DATES_CACHE_TTL_MS, loadTradeLogDates),
    cachedRead("portfolio:contractOpenDates", TRADE_LOG_DATES_CACHE_TTL_MS, loadContractOpenDates),
  ]);
  return { trade_log_dates, contract_open_dates };
}

let bgSyncInFlight = false;
let bgSyncLastTriggeredMs = 0;
const BG_SYNC_MIN_INTERVAL_MS = 60_000;
let liveFallbackInFlight: Promise<PortfolioSnapshot> | null = null;
let liveFallbackSnapshot: PortfolioSnapshot | null = null;
let liveFallbackCachedAtMs = 0;

/** Fire-and-forget: call FastAPI background sync endpoint.
 *
 * FastAPI returns 202 in ~50ms so `bgSyncInFlight` clears almost immediately —
 * which on its own gives no rate limit when every stale GET fires this. The
 * wall-clock guard caps it to one trigger per minute regardless. */
function triggerBackgroundSync(): void {
  if (bgSyncInFlight) return;
  if (Date.now() - bgSyncLastTriggeredMs < BG_SYNC_MIN_INTERVAL_MS) return;
  bgSyncInFlight = true;
  bgSyncLastTriggeredMs = Date.now();

  console.log("[Portfolio] Background sync triggered via FastAPI");
  radonFetch("/portfolio/background-sync", { method: "POST", timeout: 5_000 })
    .then(() => {
      console.log("[Portfolio] Background sync accepted");
    })
    .catch((err) => {
      console.warn("[Portfolio] Background sync trigger failed:", err.message);
    })
    .finally(() => {
      bgSyncInFlight = false;
    });
}

async function getLiveFallbackPortfolio(): Promise<PortfolioSnapshot> {
  const now = Date.now();
  if (liveFallbackSnapshot && now - liveFallbackCachedAtMs < LIVE_FALLBACK_CACHE_TTL_MS) {
    return liveFallbackSnapshot;
  }

  if (!liveFallbackInFlight) {
    liveFallbackInFlight = radonFetch("/portfolio/sync", {
      method: "POST",
      timeout: LIVE_SYNC_TIMEOUT_MS,
    })
      .then((data) => {
        const snapshot = snapshotFromLiveSyncData(data);
        liveFallbackSnapshot = snapshot;
        liveFallbackCachedAtMs = Date.now();
        return snapshot;
      })
      .finally(() => {
        liveFallbackInFlight = null;
      });
  }

  return liveFallbackInFlight;
}

async function portfolioResponseFromSnapshot(
  snapshot: PortfolioSnapshot,
  requestId: string,
  warning?: string,
): Promise<Response> {
  const entryDates = await loadPortfolioEntryDates();
  const response = NextResponse.json({ ...snapshot.data, ...entryDates });
  if (warning) {
    response.headers.set("X-Sync-Warning", warning);
    response.headers.set("X-Portfolio-Source", "ib-live-fallback");
  }
  return setNoStoreResponseHeaders(response, requestId);
}

async function serveLiveFallback(
  requestId: string,
  reason: string,
  fallbackFailureStatus = 502,
): Promise<Response> {
  try {
    const snapshot = await getLiveFallbackPortfolio();
    return portfolioResponseFromSnapshot(snapshot, requestId, `${reason} - served live IB sync`);
  } catch (fallbackError) {
    const detail = fallbackError instanceof Error ? fallbackError.message : String(fallbackError);
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: `${reason}; live IB sync failed: ${detail}`,
        status: fallbackFailureStatus,
        code: "UPSTREAM_ERROR",
        requestId,
      }),
      requestId,
    );
  }
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  try {
    const snapshot = await cachedRead(
      "portfolio:snapshot",
      SNAPSHOT_CACHE_TTL_MS,
      readPortfolioFromDb,
      { staleWhileError: true, maxStaleMs: SNAPSHOT_MAX_STALE_MS },
    );
    if (!snapshot) {
      return serveLiveFallback(requestId, "Turso portfolio snapshot unavailable");
    }
    if (isPortfolioSnapshotStale(snapshot)) {
      triggerBackgroundSync();
    }

    return portfolioResponseFromSnapshot(snapshot, requestId);
  } catch (error) {
    // A read timeout almost always means the cached libsql client's pool went
    // stale. Drop it, then RETRY the read once with the fresh pool before
    // spending up to 35s on a live IB sync — a valid (even stale) snapshot
    // reads in <200ms on the new client, so a transient blip never lights the
    // "LIVE DATA DEGRADED" banner when good data exists in Turso.
    resetDb();
    try {
      const retry = await readPortfolioFromDb();
      if (retry) return portfolioResponseFromSnapshot(retry, requestId);
    } catch {
      // fall through to the live IB fallback below
    }
    const message = error instanceof Error ? error.message : "Failed to read portfolio";
    return serveLiveFallback(requestId, `Turso portfolio read failed: ${message}`);
  }
}

export async function POST(): Promise<Response> {
  const requestId = getRequestId();
  try {
    const data = await radonFetch("/portfolio/sync", { method: "POST", timeout: 35_000 });
    const entryDates = await loadPortfolioEntryDates();
    const response = NextResponse.json({ ...data, ...entryDates });
    return setNoStoreResponseHeaders(response, requestId);
  } catch {
    let snapshot: PortfolioSnapshot | null = null;
    try {
      snapshot = await readPortfolioFromDb();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Sync failed and Turso portfolio read failed";
      return setNoStoreResponseHeaders(
        jsonApiError({
          message,
          status: 502,
          code: "UPSTREAM_ERROR",
          requestId,
        }),
        requestId,
      );
    }
    if (snapshot) {
      console.warn("[Portfolio] Sync failed, serving latest Turso snapshot");
      const entryDates = await loadPortfolioEntryDates();
      const res = NextResponse.json({ ...snapshot.data, ...entryDates });
      res.headers.set("X-Sync-Warning", "IB sync failed - serving latest Turso snapshot");
      return setNoStoreResponseHeaders(res, requestId);
    }
    return setNoStoreResponseHeaders(
      jsonApiError({
        message: "Sync failed and no Turso portfolio snapshot available",
        status: 502,
        code: "UPSTREAM_ERROR",
        requestId,
      }),
      requestId,
    );
  }
}
