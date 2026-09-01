import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";

/**
 * GET /api/equibles-filing-forensics?ticker=AAPL  -> one pre-trade dossier
 * GET /api/equibles-filing-forensics              -> flagged tickers, worst first
 *
 * Turso-first through dbFirstRead (equibles_filing_forensics row), disk
 * fallback data/equibles_filing_forensics/<TICKER>.json. Absent data is HTTP
 * 200 with `missing: true` — a ticker with nothing on file is a legitimate
 * empty state, not a client error. Only a malformed ticker param is a 4xx.
 *
 * `missing: true` and `data_complete: false` are NOT an all-clear: the UI must
 * render them as "no dossier yet" / "could not verify", never as "no overhang".
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const CACHE_DIR = join(process.cwd(), "..", "data", "equibles_filing_forensics");
const TICKER_RE = /^[A-Z][A-Z.]{0,9}$/;
const FLAGGED_LIST_LIMIT = 50;

// The refresh runs once per trading day (SEC filings disseminate daily), so a
// dossier older than two days means the writer is down, not that nothing filed.
const FILING_FORENSICS_MAX_AGE_MS = 48 * 60 * 60_000;

type Dossier = Record<string, unknown>;

function missingDossier(ticker: string): Dossier {
  return {
    missing: true,
    ticker,
    as_of: null,
    data_complete: false,
    flag_count: 0,
    flags: [],
    checks: [],
    sources: null,
  };
}

function normalizeTicker(raw: string): string | null {
  const upper = raw.trim().toUpperCase();
  return TICKER_RE.test(upper) ? upper : null;
}

async function readDossierFromDb(ticker: string): Promise<TimestampedRead<Dossier> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT as_of, payload FROM equibles_filing_forensics WHERE ticker = ? LIMIT 1`,
    args: [ticker],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { as_of: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Dossier,
    timestampMs: contentTimestampMs(row.as_of),
  };
}

async function readDossierFromDisk(ticker: string): Promise<TimestampedRead<Dossier> | null> {
  const raw = await readFile(join(CACHE_DIR, `${ticker}.json`), "utf-8");
  const data = JSON.parse(raw) as Dossier;
  return { data, timestampMs: contentTimestampMs(data.as_of) };
}

async function readFlaggedList(): Promise<Dossier> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT ticker, as_of, flag_count, data_complete, payload
          FROM equibles_filing_forensics
          WHERE flag_count > 0
          ORDER BY flag_count DESC, as_of DESC
          LIMIT ?`,
    args: [FLAGGED_LIST_LIMIT],
  });
  const dossiers = result.rows.map((row) => {
    const { payload } = row as unknown as { payload: string };
    return JSON.parse(payload) as Dossier;
  });
  return { dossiers, count: dossiers.length };
}

export const radonCapability = "read";

export async function GET(request: Request): Promise<Response> {
  const requestId = getRequestId();
  const rawTicker = new URL(request.url).searchParams.get("ticker");

  if (rawTicker === null) return respond(await flaggedListPayload(), requestId);

  const ticker = normalizeTicker(rawTicker);
  if (!ticker) {
    return NextResponse.json({ error: "Invalid ticker symbol" }, { status: 400 });
  }

  const result = await dbFirstRead<Dossier>({
    fromDb: () => readDossierFromDb(ticker),
    fromDisk: () => readDossierFromDisk(ticker),
    maxAgeMs: FILING_FORENSICS_MAX_AGE_MS,
    label: "equibles-filing-forensics",
  });
  if (!result.ok) return respond(missingDossier(ticker), requestId);
  return respond({ ...result.data, stale: !result.fresh }, requestId);
}

async function flaggedListPayload(): Promise<Dossier> {
  try {
    return await readFlaggedList();
  } catch {
    return { missing: true, dossiers: [], count: 0 };
  }
}

function respond(payload: Dossier, requestId: string): Response {
  return setCacheResponseHeaders(NextResponse.json(payload), {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["equibles-filing-forensics"],
  });
}
