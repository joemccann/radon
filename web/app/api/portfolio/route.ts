import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { dbExecute } from "@/lib/dbExecute";
import { buildContractEntryDates, type JournalEntryRow } from "@/lib/entryDates";
import {
  invalidatePortfolioReadCaches,
  readCachedPortfolioContractOpenDates,
  readCachedPortfolioTradeLogDates,
} from "@/lib/portfolio/portfolioReadCache";
import {
  PortfolioSnapshotCorruptError,
  readPortfolioFromDb,
  readPortfolioSnapshot,
  type PortfolioSnapshot,
  withoutPortfolioEntryDates,
} from "@/lib/portfolio/readPortfolioSnapshot.server";

// Disable Next.js static caching: this handler reads live Turso state.
// Without this, the framework freezes the first response and serves stale
// data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const DB_READ_TIMEOUT_MS = 3_000;

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
    // `right` is a SQL reserved word, so it is aliased opt_right in the query;
    // map it back to the JournalEntryRow field the builder reads. Coerce
    // contracts to a number in case the driver hands it back as text.
    const rows: JournalEntryRow[] = result.rows.map((row) => {
      const r = row as unknown as {
        ticker?: string; expiry?: string; opt_right?: string;
        strike?: number | string; action?: string; contracts?: number | string; date?: string;
      };
      return {
        ticker: r.ticker,
        expiry: r.expiry,
        right: r.opt_right,
        strike: r.strike,
        action: r.action,
        contracts: r.contracts == null ? null : Number(r.contracts),
        date: r.date,
      };
    });
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
    readCachedPortfolioTradeLogDates(loadTradeLogDates),
    readCachedPortfolioContractOpenDates(loadContractOpenDates),
  ]);
  return { trade_log_dates, contract_open_dates };
}

async function portfolioResponseFromSnapshot(
  snapshot: PortfolioSnapshot,
  requestId: string,
  warning: string | null,
  includeEntryDates: boolean,
): Promise<Response> {
  const entryDates = includeEntryDates ? await loadPortfolioEntryDates() : {};
  const response = NextResponse.json({ ...snapshot.data, ...entryDates });
  if (warning) {
    response.headers.set("X-Sync-Warning", warning);
    response.headers.set("X-Portfolio-Source", "turso-stale");
  }
  return setNoStoreResponseHeaders(response, requestId);
}

/** An unparseable stored payload is persistence corruption, not an outage.
 *  Reported as DB_UNAVAILABLE it sends the operator to restart Turso, which
 *  cannot help: the repair is re-running the portfolio sync. R-257. */
function corruptPortfolioResponse(
  requestId: string,
  error: PortfolioSnapshotCorruptError,
): Response {
  console.error("[portfolio] stored snapshot is corrupt:", error.message);
  return setNoStoreResponseHeaders(
    jsonApiError({
      message: `Stored portfolio snapshot is corrupt; re-run the portfolio sync: ${error.message}`,
      status: 500,
      code: "SNAPSHOT_CORRUPT",
      requestId,
    }),
    requestId,
  );
}

function unavailablePortfolioResponse(
  requestId: string,
  message: string,
): Promise<Response> {
  return Promise.resolve(setNoStoreResponseHeaders(
    jsonApiError({
      message,
      status: 503,
      code: "DB_UNAVAILABLE",
      requestId,
    }),
    requestId,
  ));
}

export const radonCapability = { GET: "read", POST: "mutate.workspace" };

export async function GET(request?: Request): Promise<Response> {
  const access = await requireRouteAccess(request, { rate: { key: "portfolio:route", limit: 20, windowMs: 60_000 }, durableRateTier: "I" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const includeEntryDates = request
    ? new URL(request.url).searchParams.get("include") === "entry-dates"
    : false;
  try {
    const read = await readPortfolioSnapshot();
    if (!read) {
      return unavailablePortfolioResponse(requestId, "Portfolio snapshot unavailable");
    }
    return portfolioResponseFromSnapshot(
      read.snapshot,
      requestId,
      read.warning,
      includeEntryDates,
    );
  } catch (error) {
    if (error instanceof PortfolioSnapshotCorruptError) {
      return corruptPortfolioResponse(requestId, error);
    }
    const message = error instanceof Error ? error.message : "Failed to read portfolio";
    return unavailablePortfolioResponse(requestId, `Turso portfolio read failed: ${message}`);
  }
}

export async function POST(): Promise<Response> {
  const access = await requireRouteAccess(undefined, {
    operatorOnly: true,
    rate: { key: "portfolio-sync", limit: 4, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const data = await radonFetch("/portfolio/sync", { method: "POST", timeout: 35_000 });
    // The sync can publish both a portfolio snapshot and new journal fills;
    // do not merge its live response with pre-sync entry-date maps.
    invalidatePortfolioReadCaches();
    const response = NextResponse.json(
      withoutPortfolioEntryDates(data),
    );
    return setNoStoreResponseHeaders(response, requestId);
  } catch {
    // The upstream can time out after publishing both snapshot and fills.
    // Invalidate every derived read before resolving the indeterminate result.
    invalidatePortfolioReadCaches();
    let snapshot: PortfolioSnapshot | null = null;
    try {
      snapshot = await readPortfolioFromDb();
    } catch (error) {
      if (error instanceof PortfolioSnapshotCorruptError) {
        return corruptPortfolioResponse(requestId, error);
      }
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
      const res = NextResponse.json(snapshot.data);
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
