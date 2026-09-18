import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "read";

const CACHE_PATH = join(process.cwd(), "..", "data", "bounce_setup.json");
// Weekday timer after the close.
const STALE_THRESHOLD_SECONDS = 26 * 60 * 60;

export function missingBouncePayload() {
  return { missing: true, scan_time: null, results: [], bounce_count: 0 };
}

export async function readBounceCache(): Promise<Record<string, unknown> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  return JSON.parse(raw) as Record<string, unknown>;
}

async function readBounceFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const result = await getDb().execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'bounce-setup' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readBounceFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const data = await readBounceCache();
  if (data == null) return null;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess();
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const result = await dbFirstRead({
    fromDb: readBounceFromDb,
    fromDisk: readBounceFromDisk,
    maxAgeMs: STALE_THRESHOLD_SECONDS * 1000,
    label: "bounce-setup",
  });
  if (result.ok) {
    const ts = result.timestampMs;
    const cache_meta = {
      last_refresh: ts == null ? null : new Date(ts).toISOString(),
      age_seconds: ts == null ? null : Math.max(0, Math.round((Date.now() - ts) / 1000)),
      is_stale: !result.fresh,
      stale_threshold_seconds: STALE_THRESHOLD_SECONDS,
    };
    return setNoStoreResponseHeaders(NextResponse.json({ ...result.data, cache_meta }), requestId);
  }
  return setNoStoreResponseHeaders(NextResponse.json(missingBouncePayload()), requestId);
}
