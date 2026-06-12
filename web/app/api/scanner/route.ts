import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { statSync } from "fs";
import { join } from "path";
import { radonFetch } from "@/lib/radonApi";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "scanner.json");
const STALE_THRESHOLD_SECONDS = 600;

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

/** Phase 2 — latest Turso snapshot, timestamped by the scan_time row key. */
async function readScannerFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scanner_snapshots ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readScannerFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  // Fresher of DB row and disk JSON. The cache_meta still reflects file
  // mtime — useful for "how stale is the disk fallback" diagnostics.
  const result = await dbFirstRead({
    fromDb: readScannerFromDb,
    fromDisk: readScannerFromDisk,
    maxAgeMs: STALE_THRESHOLD_SECONDS * 1000,
    label: "scanner",
  });
  const cache_meta = buildCacheMeta(CACHE_PATH);
  if (result.ok) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...result.data, cache_meta }),
      requestId,
    );
  }
  return setNoStoreResponseHeaders(
    NextResponse.json({
      scan_time: "",
      tickers_scanned: 0,
      signals_found: 0,
      top_signals: [],
      cache_meta,
    }),
    requestId,
  );
}

export async function POST(): Promise<Response> {
  const requestId = getRequestId();
  try {
    const data = await radonFetch("/scan", { method: "POST", timeout: 130_000 });
    const cache_meta = buildCacheMeta(CACHE_PATH);
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...data, cache_meta }),
      requestId,
    );
  } catch (error) {
    // Serve cached data on failure
    try {
      const raw = await readFile(CACHE_PATH, "utf-8");
      const cached = JSON.parse(raw);
      const cache_meta = buildCacheMeta(CACHE_PATH);
      const res = NextResponse.json({ ...cached, cache_meta, is_stale: true });
      res.headers.set("X-Sync-Warning", "Radon API unavailable - serving cached data");
      return setNoStoreResponseHeaders(res, requestId);
    } catch {
      const message = error instanceof Error ? error.message : "Scanner failed";
      return setNoStoreResponseHeaders(
        NextResponse.json({ error: message }, { status: 502 }),
        requestId,
      );
    }
  }
}
