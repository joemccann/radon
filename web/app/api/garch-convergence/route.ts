import { NextResponse } from "next/server";
import { cachedRead } from "@/lib/dbCache";
import { dbExecute } from "@/lib/dbExecute";
import { snapshotCacheMeta } from "@/lib/snapshotCacheMeta";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";

/**
 * GET /api/garch-convergence
 *
 * Serves the latest GARCH convergence scan from the Turso scan_snapshots row
 * (service "garch-scan", mirrored by garch_convergence.py), or the last good
 * read held in memory when Turso fails.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const STALE_THRESHOLD_SECONDS = 6 * 60 * 60;
// Coalesces polling tabs into one source read per window
// (contract: tests/db-read-cache-contract.test.ts).
const READ_CACHE_TTL_MS = 10_000;

async function readGarchFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const result = await dbExecute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'garch-scan' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  }, { label: "garch-convergence" });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const result = await cachedRead("garch:snapshot", READ_CACHE_TTL_MS, () =>
    dbFirstRead({
      fromDb: readGarchFromDb,
      maxAgeMs: STALE_THRESHOLD_SECONDS * 1000,
      label: "garch-convergence",
    }),
  );
  const cache_meta = snapshotCacheMeta(result.ok ? result.timestampMs : null, STALE_THRESHOLD_SECONDS);
  if (result.ok) {
    return setNoStoreResponseHeaders(
      NextResponse.json({ ...result.data, cache_meta }),
      requestId,
    );
  }
  return setNoStoreResponseHeaders(
    NextResponse.json({
      scan_time: "",
      tickers: {},
      pairs: [],
      cache_meta,
    }),
    requestId,
  );
}
