import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { cachedRead } from "@/lib/dbCache";
import { dbExecute } from "@/lib/dbExecute";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

/**
 * GET /api/catalysts
 *
 * Serves the latest catalyst feed — fresher of the Turso `catalysts` row
 * (written by `scripts/fetch_catalysts.py`) and the `data/catalysts.json`
 * disk fallback. 200 + `missing` flag when neither source has been written
 * yet — never 4xx for a legitimate empty state.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const CATALYSTS_CACHE_PATH = join(process.cwd(), "..", "data", "catalysts.json");
// Snapshot availability budget; service health separately enforces the
// producer's 06:30 / 10:00 / 16:00 ET active-day cadence.
const CATALYSTS_MAX_AGE_MS = 26 * 60 * 60 * 1000;
// Coalesces polling tabs into one source read per window
// (contract: tests/db-read-cache-contract.test.ts).
const READ_CACHE_TTL_MS = 10_000;

type CatalystsPayload = {
  scan_time: string | null;
  count: number;
  catalysts: unknown[];
};

/** The Turso row stores the raw catalyst rows array keyed by scan_time. */
async function readCatalystsFromDb(): Promise<TimestampedRead<CatalystsPayload> | null> {
  const result = await dbExecute({
    sql: `SELECT scan_time, payload FROM catalysts ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  }, { label: "catalysts" });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  const rows = JSON.parse(row.payload);
  const catalysts = Array.isArray(rows) ? rows : [];
  return {
    data: { scan_time: row.scan_time, count: catalysts.length, catalysts },
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readCatalystsFromDisk(): Promise<TimestampedRead<CatalystsPayload> | null> {
  const raw = await readFile(CATALYSTS_CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as CatalystsPayload;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await cachedRead("catalysts:snapshot", READ_CACHE_TTL_MS, () =>
    dbFirstRead({
      fromDb: readCatalystsFromDb,
      fromDisk: readCatalystsFromDisk,
      maxAgeMs: CATALYSTS_MAX_AGE_MS,
      label: "catalysts",
    }),
  );
  if (result.ok) {
    return setNoStoreResponseHeaders(NextResponse.json(result.data), requestId);
  }
  return setNoStoreResponseHeaders(
    NextResponse.json({ missing: true, scan_time: null, count: 0, catalysts: [] }),
    requestId,
  );
}
