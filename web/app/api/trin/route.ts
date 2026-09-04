import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setCacheResponseHeaders, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead, staleCollapse, isMissingPayload } from "@/lib/dbFirstRead";
import { MISSING_TRIN } from "@/lib/trin";
import { requireRouteAccess } from "@/lib/routeAccess";
import { buildDemoTrinFixture } from "@/lib/demo/fixtures/regime";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "trin.json");

// radon-trin.timer samples IB every 5 minutes during RTH; 30 minutes
// tolerates a few missed cycles before the snapshot is treated as dead.
// Off-hours the last snapshot is the close and the panel shows its timestamp.
const TRIN_MAX_AGE_MS = 30 * 60_000;

async function readTrinFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'trin' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readTrinFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const data = JSON.parse(raw) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess();
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  if (access.principal.kind === "demo") {
    return setNoStoreResponseHeaders(NextResponse.json(buildDemoTrinFixture()), requestId);
  }
  const result = await dbFirstRead({
    fromDb: readTrinFromDb,
    fromDisk: readTrinFromDisk,
    maxAgeMs: TRIN_MAX_AGE_MS,
    label: "trin",
    // R-193: a writer that ran, produced nothing and still stamped a
    // timestamped row would otherwise outrank an older row with a real
    // series on freshness alone. Same guard vixcor and ivrank carry.
    isDegraded: isMissingPayload,
  });
  const response = NextResponse.json(
    result.ok && result.fresh ? result.data : staleCollapse(MISSING_TRIN, result),
  );
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 60,
    staleWhileRevalidateSeconds: 300,
    requestId,
    cacheState: "HIT",
    tags: ["trin"],
  });
}
