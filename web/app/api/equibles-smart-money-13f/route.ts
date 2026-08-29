import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import {
  getRequestId,
  setCacheResponseHeaders,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { dbExecute } from "@/lib/dbExecute";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";

/**
 * GET /api/equibles-smart-money-13f?ticker=AAPL
 *
 * 13F institutional positioning depth (Equibles) — position-level holders,
 * the QoQ filer series, largest buyers/sellers, and named super investors.
 * Complements the thin UW institutional summary on /api/informed-flow.
 *
 * Reads through the dbFirstRead chokepoint: the newest Turso vintage for the
 * ticker (equibles_13f_snapshots), disk data/equibles_13f/{TICKER}.json
 * fallback. Absent data is the contract's `missing: true` shape at HTTP 200 —
 * a ticker no institution has filed on is a legitimate empty state, not an
 * error. A malformed or absent ticker IS a real client error and 400s.
 */
// Disable Next.js static caching: this handler reads live disk state.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const TICKER_RE = /^[A-Z]{1,5}$/;
const CACHE_DIR = join(process.cwd(), "..", "data", "equibles_13f");

// 13F is quarterly, so the refresh job runs weekly against a source that only
// moves four times a year. Beyond two refresh cycles the writer is down, not
// the source. Freshness only ranks db-vs-disk here; both are served either way.
const SNAPSHOT_MAX_AGE_MS = 14 * 24 * 60 * 60_000;

function missingSurface(ticker: string): Record<string, unknown> {
  return {
    ticker,
    missing: true,
    scan_time: null,
    report_date: null,
    disclosure: null,
    headline: null,
    holders: [],
    holder_count: 0,
    ownership_series: [],
    activity: { buyers: [], sellers: [] },
    super_investors: [],
    market_context: null,
  };
}

function normalizeTicker(raw: string | null): string | null {
  const upper = (raw ?? "").trim().toUpperCase();
  return TICKER_RE.test(upper) ? upper : null;
}

async function readFromDb(
  ticker: string,
): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const result = await dbExecute(
    {
      sql: `SELECT scan_time, payload FROM equibles_13f_snapshots
            WHERE ticker = ? ORDER BY report_date DESC LIMIT 1`,
      args: [ticker],
    },
    { label: "equibles-13f" },
  );
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readFromDisk(
  ticker: string,
): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(join(CACHE_DIR, `${ticker}.json`), "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time as string) };
}

export const radonCapability = "read";

export async function GET(request: Request): Promise<Response> {
  const requestId = getRequestId();
  const ticker = normalizeTicker(new URL(request.url).searchParams.get("ticker"));
  if (!ticker) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: "A valid ticker symbol is required" }, { status: 400 }),
      requestId,
    );
  }

  const result = await dbFirstRead({
    fromDb: () => readFromDb(ticker),
    fromDisk: () => readFromDisk(ticker),
    maxAgeMs: SNAPSHOT_MAX_AGE_MS,
    label: `equibles-smart-money-13f:${ticker}`,
  });

  const response = NextResponse.json(result.ok ? result.data : missingSurface(ticker));
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["equibles-smart-money-13f"],
  });
}
