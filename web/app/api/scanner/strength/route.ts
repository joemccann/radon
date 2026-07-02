import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { statSync } from "fs";
import { join } from "path";
import { getDb } from "@/lib/db";
import { cachedRead } from "@/lib/dbCache";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "strength_confirmation.json");
const STALE_THRESHOLD_SECONDS = 6 * 60 * 60;
// Coalesces polling tabs into one source read per window
// (contract: tests/db-read-cache-contract.test.ts).
const READ_CACHE_TTL_MS = 10_000;

type CacheMeta = {
  last_refresh: string | null;
  age_seconds: number | null;
  is_stale: boolean;
  stale_threshold_seconds: number;
};

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

export function emptyStrengthConfirmationPayload() {
  return {
    scan_time: "",
    source: "Unusual Whales + Radon regime caches",
    universe: "preset:ndx100",
    requested_tickers: [],
    tickers_scanned: 0,
    candidates_found: 0,
    confirmed_strength_count: 0,
    results: [],
  };
}

export async function readStrengthConfirmationCache(): Promise<Record<string, unknown> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  return JSON.parse(raw) as Record<string, unknown>;
}

/** Latest Turso snapshot — shared across hosts, so a scan that ran on the
 *  FastAPI host is visible to the Next.js host (the disk file is host-local
 *  and there is no strength auto-scan timer). */
async function readStrengthFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM strength_confirmation_snapshots ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readStrengthFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const data = await readStrengthConfirmationCache();
  if (data == null) return null;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const cache_meta = buildCacheMeta(CACHE_PATH);
  // Fresher of the shared Turso snapshot and the host-local disk JSON.
  const result = await cachedRead("scanner:strength", READ_CACHE_TTL_MS, () =>
    dbFirstRead({
      fromDb: readStrengthFromDb,
      fromDisk: readStrengthFromDisk,
      maxAgeMs: STALE_THRESHOLD_SECONDS * 1000,
      label: "strength-confirmation",
    }),
  );
  if (result.ok) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...result.data, cache_meta }),
      requestId,
    );
  }
  return setNoStoreResponseHeaders(
    NextResponse.json({ ...emptyStrengthConfirmationPayload(), cache_meta }),
    requestId,
  );
}
