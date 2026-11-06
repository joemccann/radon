import { NextResponse } from "next/server";
import { cachedRead } from "@/lib/dbCache";
import { dbExecute } from "@/lib/dbExecute";
import { snapshotCacheMeta } from "@/lib/snapshotCacheMeta";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
// Disable Next.js static caching: this handler reads live Turso state. Without
// this, the framework freezes the first response and serves stale data until
// the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const STALE_THRESHOLD_SECONDS = 600;
// Coalesces polling tabs into one source read per window
// (contract: tests/db-read-cache-contract.test.ts).
const READ_CACHE_TTL_MS = 10_000;

interface FlowSurpriseFile {
  scan_time?: string;
  metric?: string;
  count_ok?: number;
  results?: unknown[];
  skipped?: number;
}

async function readFlowSurpriseFromDb(): Promise<TimestampedRead<FlowSurpriseFile> | null> {
  const result = await dbExecute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'flow-surprise' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  }, { label: "flow-surprise" });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as FlowSurpriseFile,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await cachedRead("flow-surprise:snapshot", READ_CACHE_TTL_MS, () =>
    dbFirstRead({
      fromDb: readFlowSurpriseFromDb,
      maxAgeMs: STALE_THRESHOLD_SECONDS * 1000,
      label: "flow-surprise",
    }),
  );
  const cache_meta = snapshotCacheMeta(result.ok ? result.timestampMs : null, STALE_THRESHOLD_SECONDS);
  if (result.ok) {
    const data = result.data;
    return setNoStoreResponseHeaders(
      NextResponse.json({
        results: Array.isArray(data.results) ? data.results : [],
        metric: data.metric ?? "flow_strength",
        scan_time: data.scan_time ?? "",
        count_ok: typeof data.count_ok === "number" ? data.count_ok : 0,
        skipped: typeof data.skipped === "number" ? data.skipped : 0,
        cache_meta,
      }),
      requestId,
    );
  }
  // No row and no remembered read is a legitimate empty state, not an error.
  return setNoStoreResponseHeaders(
    NextResponse.json({
      results: [],
      missing: true,
      metric: "flow_strength",
      scan_time: "",
      count_ok: 0,
      skipped: 0,
      cache_meta,
    }),
    requestId,
  );
}
