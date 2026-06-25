import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { withTimeout } from "@/lib/asyncTimeout";

// Disable Next.js static caching: this handler reads live Turso state.
// Without this, the framework freezes the first response and serves stale
// data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_TTL_MS = 60_000; // 1 minute
const DB_READ_TIMEOUT_MS = 3_000;

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
  const db = getDb();
  const result = await withTimeout(
    db.execute({
      sql: `SELECT taken_at, payload FROM portfolio_snapshots ORDER BY taken_at DESC LIMIT 1`,
      args: [],
    }),
    DB_READ_TIMEOUT_MS,
    `portfolio snapshot read timed out after ${DB_READ_TIMEOUT_MS}ms`,
  );
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

/** Load ticker → latest trade date.
 *
 * The canonical source is the `journal` table. Missing rows or query
 * failures return an empty map; this route never falls back to
 * the old flat trade-log mirror.
 */
async function loadTradeLogDates(): Promise<Record<string, string>> {
  try {
    const db = getDb();
    const result = await withTimeout(
      db.execute({
        sql: `
        SELECT
          json_extract(payload, '$.ticker') AS ticker,
          MAX(COALESCE(filled_at, json_extract(payload, '$.date'))) AS date
        FROM journal
        WHERE json_extract(payload, '$.ticker') IS NOT NULL
        GROUP BY json_extract(payload, '$.ticker')
      `,
        args: [],
      }),
      DB_READ_TIMEOUT_MS,
      `portfolio trade-log date read timed out after ${DB_READ_TIMEOUT_MS}ms`,
    );
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

let bgSyncInFlight = false;

/** Fire-and-forget: call FastAPI background sync endpoint */
function triggerBackgroundSync(): void {
  if (bgSyncInFlight) return;
  bgSyncInFlight = true;

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

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  try {
    const snapshot = await readPortfolioFromDb();
    if (isPortfolioSnapshotStale(snapshot)) {
      triggerBackgroundSync();
    }
    if (!snapshot) {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: "Portfolio data not found",
          status: 404,
          code: "NOT_FOUND",
          requestId,
        }),
        requestId,
      );
    }

    const tradeLogDates = await loadTradeLogDates();
    const response = NextResponse.json({ ...snapshot.data, trade_log_dates: tradeLogDates });
    return setNoStoreResponseHeaders(response, requestId);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to read portfolio";
    return setNoStoreResponseHeaders(
      jsonApiError({
        message,
        status: 500,
        code: "INTERNAL_ERROR",
        requestId,
      }),
      requestId,
    );
  }
}

export async function POST(): Promise<Response> {
  const requestId = getRequestId();
  try {
    const data = await radonFetch("/portfolio/sync", { method: "POST", timeout: 35_000 });
    const tradeLogDates = await loadTradeLogDates();
    const response = NextResponse.json({ ...data, trade_log_dates: tradeLogDates });
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
      const tradeLogDates = await loadTradeLogDates();
      const res = NextResponse.json({ ...snapshot.data, trade_log_dates: tradeLogDates });
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
