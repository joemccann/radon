import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { getRequestId, setCacheResponseHeaders, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, isMissingPayload, staleCollapse, type TimestampedRead } from "@/lib/dbFirstRead";
import { MISSING_DISPERSION } from "@/lib/dispersion";
import { getFreshnessWindowMs } from "@/lib/serviceHealthWindows";
import { requireRouteAccess } from "@/lib/routeAccess";
import { buildDemoDispersionFixture } from "@/lib/demo/fixtures/regime";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "dispersion.json");

// radon-dispersion.timer runs the IB daily-bar sweep at 22:20 UTC on every
// calendar day — weekend and holiday runs are no-new-session heartbeats — so
// a snapshot older than the catalog's uniform 26h window means the writer is
// down. The panel's writer age and the watchdog read the same window; a
// private 48h here let hours 26-48 render a confident regime beside a
// `behind` age (R-450).
const DISPERSION_MAX_AGE_MS = getFreshnessWindowMs("dispersion", "closed");

async function readDispersionFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM scan_snapshots
          WHERE service = 'dispersion' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

async function readDispersionFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
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
    return setNoStoreResponseHeaders(NextResponse.json(buildDemoDispersionFixture()), requestId);
  }
  const result = await dbFirstRead({
    fromDb: readDispersionFromDb,
    fromDisk: readDispersionFromDisk,
    maxAgeMs: DISPERSION_MAX_AGE_MS,
    label: "dispersion",
    // A fresher row that only carries the missing:true heartbeat must not
    // outrank an older row with a real series — source selection was on
    // timestamp alone and regressed the tab to the empty state. R-366.
    isDegraded: isMissingPayload,
  });
  // `result.fresh` is computed from DISPERSION_MAX_AGE_MS and must be honoured:
  // discarding it let a dead writer keep serving a week-old snapshot with no
  // stale or missing marker, and the panel rendered a confident regime badge
  // for a dead feed. Same shape as vixts/route.ts. R-332.
  const response = NextResponse.json(
    result.ok && result.fresh ? result.data : staleCollapse(MISSING_DISPERSION, result),
  );
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 300,
    staleWhileRevalidateSeconds: 3600,
    requestId,
    cacheState: "HIT",
    tags: ["dispersion"],
  });
}
