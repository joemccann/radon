import { PORTFOLIO_GET_LIMIT } from "@/lib/accountPollBudget";
import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { radonFetch } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  scrubSecrets,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { dbBatch, dbExecute } from "@/lib/dbExecute";
import { PRODUCER_SYNC_WAIT_MS } from "@/lib/edgeBudget";
import { markSyncFallback } from "@/lib/syncFallback";
import { demoDbIsolationViolation } from "@/lib/demo/demoDbIsolation";
import { buildContractEntryDates, type JournalEntryRow } from "@/lib/entryDates";
import {
  invalidatePortfolioReadCaches,
  readCachedPortfolioEntryDates,
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

/** Ticker expression and IS NOT NULL filter match idx_journal_ticker
 * (migration 0095) textually; change one, change both. */
const TRADE_LOG_DATES_SQL = `
        SELECT
          json_extract(payload, '$.ticker') AS ticker,
          MAX(COALESCE(filled_at, json_extract(payload, '$.date'))) AS date
        FROM journal
        WHERE json_extract(payload, '$.ticker') IS NOT NULL
        GROUP BY json_extract(payload, '$.ticker')
      `;

/** Right/strike WHERE clause matches idx_journal_option_contract
 * (migration 0095) textually; change one, change both. */
const CONTRACT_OPEN_DATES_SQL = `
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
      `;

function tradeLogDatesFromRows(rows: unknown[]): Record<string, string> {
  const dates: Record<string, string> = {};
  for (const row of rows) {
    const r = row as { ticker?: string; date?: string };
    if (typeof r.ticker === "string" && typeof r.date === "string") {
      dates[r.ticker] = r.date;
    }
  }
  return dates;
}

function contractOpenDatesFromRows(rows: unknown[]): Record<string, string> {
  // `right` is a SQL reserved word, so it is aliased opt_right in the query;
  // map it back to the JournalEntryRow field the builder reads. Coerce
  // contracts to a number in case the driver hands it back as text.
  const parsed: JournalEntryRow[] = rows.map((row) => {
    const r = row as {
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
  return buildContractEntryDates(parsed);
}

/** Load ticker → latest trade date and per-contract opening-episode date.
 *
 * The canonical source is the `journal` table. Missing rows or query
 * failures return empty maps; this route never falls back to
 * the old flat trade-log mirror.
 *
 * One `/v2/pipeline` for both SELECTs (PERF-025). WorkspaceShell polls
 * `?include=entry-dates` every 2s on /orders during RTH; the maps share a
 * 60s cache so a miss used to issue two Turso HTTP round-trips.
 */
async function loadPortfolioEntryDatesFromDb(): Promise<{
  trade_log_dates: Record<string, string>;
  contract_open_dates: Record<string, string>;
}> {
  try {
    const [tradeResult, contractResult] = await dbBatch(
      [
        { sql: TRADE_LOG_DATES_SQL, args: [] },
        { sql: CONTRACT_OPEN_DATES_SQL, args: [] },
      ],
      { label: "portfolio entry dates", timeoutMs: DB_READ_TIMEOUT_MS, mode: "read" },
    );
    return {
      trade_log_dates: tradeLogDatesFromRows(tradeResult.rows),
      contract_open_dates: contractOpenDatesFromRows(contractResult.rows),
    };
  } catch (error) {
    // dbExecute/dbBatch scrub their own diagnostic and rethrow the ORIGINAL
    // error, so a raw copy here put libsql URLs, bearer tokens and account
    // ids in the server log. Route it through the same scrubber.
    const message = error instanceof Error ? error.message : String(error);
    console.warn(
      scrubSecrets(`[Portfolio] entry-dates journal query failed: ${message}`),
    );
    return { trade_log_dates: {}, contract_open_dates: {} };
  }
}

/** The two journal-derived entry-date maps the share card consults. */
async function loadPortfolioEntryDates(): Promise<{
  trade_log_dates: Record<string, string>;
  contract_open_dates: Record<string, string>;
}> {
  return readCachedPortfolioEntryDates(loadPortfolioEntryDatesFromDb);
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
  const access = await requireRouteAccess(request, { rate: { key: "portfolio:route", limit: PORTFOLIO_GET_LIMIT, windowMs: 60_000 }, durableRateTier: "I" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  // REL-245: demo principal implies demo DB. On a prod-marked TURSO_DB_URL
  // refuse before any DB read — never the operator's live portfolio.
  if (access.principal.kind === "demo") {
    const violation = demoDbIsolationViolation();
    if (violation) {
      console.error(`[Portfolio] ${violation}`);
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: "Demo portfolio is unavailable",
          status: 403,
          code: "DEMO_DB_ISOLATION",
          requestId,
        }),
        requestId,
      );
    }
  }
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
    const data = await radonFetch("/portfolio/sync", { method: "POST", timeout: PRODUCER_SYNC_WAIT_MS });
    // The sync can publish both a portfolio snapshot and new journal fills;
    // do not merge its live response with pre-sync entry-date maps.
    invalidatePortfolioReadCaches();
    const response = NextResponse.json(
      withoutPortfolioEntryDates(data),
    );
    return setNoStoreResponseHeaders(response, requestId);
  } catch (syncError) {
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
      const res = markSyncFallback(NextResponse.json(snapshot.data), syncError);
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
