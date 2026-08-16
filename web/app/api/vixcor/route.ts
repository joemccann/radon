import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import {
  contentTimestampMs,
  dbFirstRead,
  isMissingPayload,
  type TimestampedRead,
} from "@/lib/dbFirstRead";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "vixcor.json");

// Contract: absent vixcor data is HTTP 200 with missing:true, never a 4xx.
// The job's own degradation states ("holding", "stale_parent") are real
// payloads and pass straight through; only "no snapshot anywhere" and
// "every snapshot older than the freshness budget" collapse to this shape.
const MISSING_VIXCOR = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  count: 0,
  series: [],
  episodes: [],
  current: null,
  stats: null,
  forward_stats: null,
};

// radon-vixcor.timer fires daily at 02:35 UTC including weekends (Cboe
// re-touch days heartbeat via conditional GET), so a snapshot older than
// two days means the writer is down.
const VIXCOR_MAX_AGE_MS = 48 * 60 * 60_000;

async function readVixcorFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'vixcor' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readVixcorFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readVixcorFromDb,
    fromDisk: readVixcorFromDisk,
    maxAgeMs: VIXCOR_MAX_AGE_MS,
    label: "vixcor",
    // A fresher row that only carries the missing:true heartbeat must not
    // outrank an older row with a real series. The job's "holding" and
    // "stale_parent" payloads are NOT degraded by this test: they carry a
    // full series and reach the client verbatim.
    isDegraded: isMissingPayload,
  });
  const response = NextResponse.json(result.ok && result.fresh ? result.data : MISSING_VIXCOR);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["vixcor"],
  });
}
