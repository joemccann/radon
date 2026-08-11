import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";

// Disable Next.js static caching: this handler reads live disk state
// (data/equibles_short_crowding.json). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "equibles_short_crowding.json");

const SERVICE = "equibles-short-crowding";

// Contract: absent short-crowding data is HTTP 200 with missing:true, never a 4xx.
const MISSING_SHORT_CROWDING = {
  missing: true,
  scan_time: null,
  count: 0,
  latest_settlement_date: null,
  entries: [],
  board: [],
};

// The refresh timer runs daily (the squeeze score moves daily even though
// short interest settles bi-monthly), so a snapshot older than two days means
// the writer is down.
const SHORT_CROWDING_MAX_AGE_MS = 48 * 60 * 60_000;

async function readShortCrowdingFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = ? ORDER BY scan_time DESC LIMIT 1`,
    args: [SERVICE],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readShortCrowdingFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

// An entry-less payload is a degraded writer cycle, not a real scoreboard:
// it must never beat an older populated snapshot on timestamp alone.
function isDegraded(data: Record<string, unknown>): boolean {
  return data.missing === true || !Array.isArray(data.entries) || data.entries.length === 0;
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readShortCrowdingFromDb,
    fromDisk: readShortCrowdingFromDisk,
    maxAgeMs: SHORT_CROWDING_MAX_AGE_MS,
    label: SERVICE,
    isDegraded,
  });
  const serveMissing = !result.ok || isDegraded(result.data);
  const response = NextResponse.json(serveMissing ? MISSING_SHORT_CROWDING : result.data);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: [SERVICE],
  });
}
