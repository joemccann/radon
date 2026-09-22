import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead, staleCollapse } from "@/lib/dbFirstRead";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json). Without this, the framework freezes the first response and
// serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "calm_streak.json");

// Contract: absent CALM STREAK data is HTTP 200 with missing:true, never a 4xx.
// Defined locally on purpose: web/lib/calmStreak.ts belongs to the UI layer.
const MISSING_CALM_STREAK = Object.freeze({
  missing: true,
  scan_time: null,
  data_date: null,
  current: null,
  stats: null,
  series: [],
});

// radon-calm-streak.timer fires twice every calendar day (02:40, 14:30 UTC);
// older than 48h means the writer is down, not merely between runs.
const CALM_STREAK_MAX_AGE_MS = 48 * 60 * 60_000;

async function readCalmStreakFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'calm-streak' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readCalmStreakFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readCalmStreakFromDb,
    fromDisk: readCalmStreakFromDisk,
    maxAgeMs: CALM_STREAK_MAX_AGE_MS,
    label: "calm-streak",
  });
  // Past CALM_STREAK_MAX_AGE_MS the writer is down: collapse to missing +
  // stale + scan_time (R-125 shape) instead of serving dead data.
  const response = NextResponse.json(
    result.ok && result.fresh ? result.data : staleCollapse(MISSING_CALM_STREAK, result),
  );
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["calm-streak"],
  });
}
