import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { statSync } from "fs";
import { join } from "path";
import { getDb } from "@/lib/db";
import { cachedRead } from "@/lib/dbCache";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

/**
 * GET /api/leap
 *
 * Serves the latest LEAP IV-mispricing scan — fresher of the Turso
 * scan_snapshots row (service "leap-scan", mirrored by leap_scanner_uw.py)
 * and the `data/leap.json` disk fallback.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const LEAP_CACHE_PATH = join(process.cwd(), "..", "data", "leap.json");
const STALE_THRESHOLD_SECONDS = 6 * 60 * 60; // LEAP scans are slow + low-cadence
// Coalesces polling tabs into one source read per window
// (contract: tests/db-read-cache-contract.test.ts).
const READ_CACHE_TTL_MS = 10_000;

interface CacheMeta {
  last_refresh: string | null;
  age_seconds: number | null;
  is_stale: boolean;
  stale_threshold_seconds: number;
}

function buildCacheMeta(filePath: string): CacheMeta {
  try {
    const s = statSync(filePath);
    const ageSeconds = (Date.now() - s.mtime.getTime()) / 1000;
    return {
      last_refresh: s.mtime.toISOString(),
      age_seconds: Math.round(ageSeconds),
      is_stale: ageSeconds > STALE_THRESHOLD_SECONDS,
      stale_threshold_seconds: STALE_THRESHOLD_SECONDS,
    };
  } catch {
    return {
      last_refresh: null,
      age_seconds: null,
      is_stale: true,
      stale_threshold_seconds: STALE_THRESHOLD_SECONDS,
    };
  }
}

async function readLeapFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const result = await getDb().execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'leap-scan' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readLeapFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(LEAP_CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await cachedRead("leap:snapshot", READ_CACHE_TTL_MS, () =>
    dbFirstRead({
      fromDb: readLeapFromDb,
      fromDisk: readLeapFromDisk,
      maxAgeMs: STALE_THRESHOLD_SECONDS * 1000,
      label: "leap",
    }),
  );
  const cache_meta = buildCacheMeta(LEAP_CACHE_PATH);
  if (result.ok) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...result.data, cache_meta }),
      requestId,
    );
  }
  return setNoStoreResponseHeaders(
    NextResponse.json({
      scan_time: "",
      min_gap: null,
      results: [],
      cache_meta,
    }),
    requestId,
  );
}
