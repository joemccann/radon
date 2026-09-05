import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { isGexDataStale } from "@/lib/gexStaleness";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { createBackgroundScanTrigger } from "@/lib/backgroundScan";
import { getRequestId, setCacheResponseHeaders, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { dbExecute } from "@/lib/dbExecute";
import { cachedRead } from "@/lib/dbCache";
import { buildDemoGexFixture } from "@/lib/demo/fixtures/regime";
// Disable Next.js static caching: this handler reads live disk state
// (data/*.json, cache files). Without this, the framework freezes the
// first response and serves stale data until the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "gex.json");

const EMPTY_GEX = {
  scan_time: "",
  market_open: false,
  ticker: "SPX",
  spot: 0,
  close: null,
  day_change: null,
  day_change_pct: null,
  data_date: "",
  net_gex: 0,
  net_dex: 0,
  atm_iv: null,
  vol_pc: null,
  levels: {
    gex_flip: null,
    max_magnet: null,
    second_magnet: null,
    max_accelerator: null,
    put_wall: null,
    call_wall: null,
  },
  profile: [],
  expected_range: { low: null, high: null, iv_1d: null },
  bias: {
    direction: "NEUTRAL",
    reasons: [],
    days_above_flip: 0,
    flip_migration: [],
  },
  history: [],
  iv: null as null | {
    iv30d: number | null;
    iv_rank: number | null;
    hv30: number | null;
    mq_iv30d: number | null;
    mq_iv_rank: string | null;
    source: "uw" | "mq" | "both" | null;
  },
  mq: null as null | Record<string, unknown>,
  source_delta: null as null | Record<string, unknown>,
};

function isMarketOpenNow(): boolean {
  const now = new Date();
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay();
  if (day === 0 || day === 6) return false;
  const minutes = et.getHours() * 60 + et.getMinutes();
  return minutes >= 9 * 60 + 30 && minutes <= 16 * 60;
}

async function readCachedGexFromDb(): Promise<Record<string, unknown> | null> {
  try {
    const result = await dbExecute({
      sql: `SELECT payload FROM gex_snapshots WHERE ticker = 'SPX' ORDER BY scan_time DESC LIMIT 1`,
      args: [],
    }, { label: "gex" });
    if (result.rows.length === 0) return null;
    const row = result.rows[0] as unknown as { payload: string };
    return JSON.parse(row.payload) as Record<string, unknown>;
  } catch {
    return null;
  }
}

async function readCachedGexFromDisk(): Promise<Record<string, unknown> | null> {
  try {
    const raw = await readFile(CACHE_PATH, "utf-8");
    const jsonStart = raw.indexOf("{");
    if (jsonStart === -1) return null;
    return JSON.parse(raw.slice(jsonStart)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** Parsed scan_time epoch ms, or 0 when absent/unparseable. */
function scanTimeMs(payload: Record<string, unknown> | null): number {
  const t = payload && typeof payload.scan_time === "string" ? Date.parse(payload.scan_time) : NaN;
  return Number.isFinite(t) ? t : 0;
}

async function readCachedGex(): Promise<Record<string, unknown> | null> {
  // Prefer whichever store has the NEWER scan_time. Reading the DB
  // unconditionally first hid fresh disk data behind a stale Turso row — GEX
  // served a week-old 06-16 snapshot while data/gex.json was current.
  const [db, disk] = await Promise.all([readCachedGexFromDb(), readCachedGexFromDisk()]);
  if (db && disk) return scanTimeMs(disk) > scanTimeMs(db) ? disk : db;
  return db ?? disk;
}

function normalizeGexPayload(raw: Record<string, unknown>): Record<string, unknown> {
  return {
    ...EMPTY_GEX,
    ...raw,
    scan_time: typeof raw.scan_time === "string" ? raw.scan_time : "",
    market_open: typeof raw.market_open === "boolean" ? raw.market_open : isMarketOpenNow(),
    ticker: typeof raw.ticker === "string" ? raw.ticker : "SPX",
    levels: typeof raw.levels === "object" && raw.levels !== null
      ? { ...EMPTY_GEX.levels, ...(raw.levels as object) }
      : EMPTY_GEX.levels,
    expected_range: typeof raw.expected_range === "object" && raw.expected_range !== null
      ? { ...EMPTY_GEX.expected_range, ...(raw.expected_range as object) }
      : EMPTY_GEX.expected_range,
    bias: typeof raw.bias === "object" && raw.bias !== null
      ? { ...EMPTY_GEX.bias, ...(raw.bias as object) }
      : EMPTY_GEX.bias,
    profile: Array.isArray(raw.profile) ? raw.profile : [],
    history: Array.isArray(raw.history) ? raw.history : [],
    iv: typeof raw.iv === "object" && raw.iv !== null ? raw.iv : null,
    mq: typeof raw.mq === "object" && raw.mq !== null ? raw.mq : null,
    source_delta: typeof raw.source_delta === "object" && raw.source_delta !== null ? raw.source_delta : null,
  };
}

const triggerBackgroundScan = createBackgroundScanTrigger({
  label: "GEX",
  run: () => radonFetch<Record<string, unknown>>("/gex/scan", { method: "POST", timeout: 130_000 }),
});

// Coalesce the polled GET reads (DB round trip + disk) into one per window.
// staleWhileError keeps GEX rendering through a brief Turso stall. The POST
// path stays uncached so a user-triggered rescan always reads the freshest.
const GEX_CACHE_TTL_MS = 5_000;

export const radonCapability = { GET: "read", POST: "read.spawn" };

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "gex:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  if (access.principal.kind === "demo") {
    return setNoStoreResponseHeaders(NextResponse.json(buildDemoGexFixture()), requestId);
  }
  const cached = await cachedRead("gex:SPX", GEX_CACHE_TTL_MS, readCachedGex, {
    staleWhileError: true,
  });
  const data = normalizeGexPayload(cached ?? {});
  const currentMarketOpen = isMarketOpenNow();

  (data as Record<string, unknown>).market_open = currentMarketOpen;

  const stale = cached
    ? isGexDataStale(cached as { scan_time?: string; market_open?: boolean }, undefined, currentMarketOpen)
    : true;

  if (stale) {
    triggerBackgroundScan();
  }

  // R-660: never label a stale snapshot HIT-fresh. The header carries the
  // cache verdict for infrastructure; the body marker is what clients read.
  (data as Record<string, unknown>).is_stale = stale;

  const response = NextResponse.json(data);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 15,
    staleWhileRevalidateSeconds: 120,
    requestId,
    cacheState: stale ? "STALE" : "HIT",
    tags: ["gex"],
  });
}

export async function POST(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "gex:route", limit: 20, windowMs: 60_000 }, durableRateTier: "B" });
  if (!access.ok) return access.response;
  if (access.principal.kind === "demo") {
    const requestId = getRequestId();
    return setNoStoreResponseHeaders(NextResponse.json(buildDemoGexFixture()), requestId);
  }
  try {
    const rawData = await radonFetch<Record<string, unknown>>("/gex/scan", { method: "POST", timeout: 130_000 });
    const data = normalizeGexPayload(rawData);
    return NextResponse.json({ ...data, scan_succeeded: true });
  } catch (err) {
    // R-643: mirror the theta scan shape — preserve the upstream status and
    // stamp the failure in the body so useSyncHook consumers see it. A 200 +
    // X-Sync-Warning header silently masked dead scans.
    const status = err instanceof RadonApiError ? err.status : 502;
    if (status >= 500) try {
      const cached = await readCachedGex();
      if (cached) {
        const res = NextResponse.json(
          { ...normalizeGexPayload(cached), is_stale: true, scan_succeeded: false },
          { status },
        );
        res.headers.set("X-Sync-Warning", "GEX sync failed - serving cached data");
        return res;
      }
    } catch {
      // Preserve the upstream failure below.
    }
    const message = err instanceof Error ? err.message : "GEX scan failed";
    return NextResponse.json({ error: message, scan_succeeded: false }, { status });
  }
}
