import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "breadth.json");

// Contract: absent breadth data is HTTP 200 with missing:true, never a 4xx.
const MISSING_BREADTH = {
  missing: true,
  scan_time: null,
  history: [],
  intraday: [],
  latest: null,
};

// Mirrors the breadth-scan open freshness window in serviceHealthWindows.ts.
const BREADTH_MAX_AGE_MS = 30 * 60_000;

async function readBreadthFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT taken_at, payload FROM breadth_snapshots ORDER BY taken_at DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { taken_at: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.taken_at),
  };
}

async function readBreadthFromDisk(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const raw = await readFile(CACHE_PATH, "utf-8");
  const jsonStart = raw.indexOf("{");
  if (jsonStart === -1) return null;
  const data = JSON.parse(raw.slice(jsonStart)) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

/** Fresher of the Turso row and data/breadth.json — a frozen writer on either side never wins. */
async function readCachedBreadth(): Promise<Record<string, unknown> | null> {
  const result = await dbFirstRead({
    fromDb: readBreadthFromDb,
    fromDisk: readBreadthFromDisk,
    maxAgeMs: BREADTH_MAX_AGE_MS,
    label: "breadth",
  });
  return result.ok ? result.data : null;
}

export const radonCapability = { GET: "read", POST: "read.spawn" };

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "breadth:route", limit: 20, windowMs: 60_000 } });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const cached = await readCachedBreadth();
  const response = NextResponse.json(cached ?? MISSING_BREADTH);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 15,
    staleWhileRevalidateSeconds: 120,
    requestId,
    cacheState: "HIT",
    tags: ["breadth"],
  });
}

export async function POST(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "breadth:route", limit: 20, windowMs: 60_000 } });
  if (!access.ok) return access.response;
  try {
    const data = await radonFetch<Record<string, unknown>>("/breadth/scan", {
      method: "POST",
      timeout: 130_000,
    });
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Breadth scan failed";
    const cached = await readCachedBreadth();
    if (cached) {
      const response = NextResponse.json(cached);
      response.headers.set("X-Sync-Warning", `Breadth sync failed: ${message}`);
      return response;
    }
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
