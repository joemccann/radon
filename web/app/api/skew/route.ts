import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, isMissingPayload, type TimestampedRead } from "@/lib/dbFirstRead";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "skew.json");

// Contract: absent skew data is HTTP 200 with missing:true, never a 4xx.
const MISSING_SKEW = {
  missing: true,
  scan_time: null,
  count: 0,
  series: [],
  current: null,
  stats: null,
};

// radon-skew.timer fires daily at 21:45 UTC including weekends (no-missing-
// sessions runs heartbeat via the fetch_skew.py fast path), so a snapshot
// older than two days means the writer is down.
const SKEW_MAX_AGE_MS = 48 * 60 * 60_000;

async function readSkewFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'skew' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readSkewFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readSkewFromDb,
    fromDisk: readSkewFromDisk,
    maxAgeMs: SKEW_MAX_AGE_MS,
    label: "skew",
    // A fresher row that only carries the missing:true heartbeat (writer
    // ran, found nothing) must not outrank an older row with a real
    // series/current/stats payload — see MISSING_SKEW / dbFirstRead docs.
    isDegraded: isMissingPayload,
  });
  const response = NextResponse.json(result.ok ? result.data : MISSING_SKEW);
  return setNoStoreResponseHeaders(response, requestId);
}
