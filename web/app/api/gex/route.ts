import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { isGexDataStale } from "@/lib/gexStaleness";
import { radonFetch, RadonApiError } from "@/lib/radonApi";
import { createBackgroundScanTrigger } from "@/lib/backgroundScan";
import { getRequestId, setCacheResponseHeaders, setNoStoreResponseHeaders, scrubSecrets } from "@/lib/apiContracts";
import { dbExecute } from "@/lib/dbExecute";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { cachedRead } from "@/lib/dbCache";
import { buildDemoGexFixture } from "@/lib/demo/fixtures/regime";
// Disable Next.js static caching: this handler reads live Turso state. Without
// this, the framework freezes the first response and serves stale data until
// the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";


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

async function readCachedGexFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const result = await dbExecute({
    sql: `SELECT payload FROM gex_snapshots WHERE ticker = 'SPX' ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  }, { label: "gex" });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { payload: string };
  const data = JSON.parse(row.payload) as Record<string, unknown>;
  return { data, timestampMs: contentTimestampMs(data.scan_time) };
}

/** Latest Turso row, or the last good read held in memory when Turso fails. */
async function readCachedGex(): Promise<Record<string, unknown> | null> {
  const result = await dbFirstRead({
    fromDb: readCachedGexFromDb,
    // Unused: isGexDataStale judges the served payload's own scan_time.
    maxAgeMs: GEX_CACHE_TTL_MS,
    label: "gex",
  });
  return result.ok ? result.data : null;
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

// Coalesce the polled GET reads into one DB round trip per window. The POST path
// stays uncached so a user-triggered rescan always reads the freshest.
const GEX_CACHE_TTL_MS = 5_000;

export const radonCapability = { GET: "read", POST: "read.spawn" };

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "gex:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  if (access.principal.kind === "demo") {
    return setNoStoreResponseHeaders(NextResponse.json(buildDemoGexFixture()), requestId);
  }
  const cached = await cachedRead("gex:SPX", GEX_CACHE_TTL_MS, readCachedGex);
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

export async function POST(request?: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "gex:route", limit: 20, windowMs: 60_000 }, durableRateTier: "B" });
  if (!access.ok) return access.response;
  if (access.principal.kind === "demo") {
    const requestId = getRequestId();
    return setNoStoreResponseHeaders(NextResponse.json(buildDemoGexFixture()), requestId);
  }
  try {
    const rawData = await radonFetch<Record<string, unknown>>("/gex/scan", {
      method: "POST",
      timeout: 130_000,
      signal: request?.signal,
    });
    const data = normalizeGexPayload(rawData);
    return NextResponse.json({ ...data, scan_succeeded: true });
  } catch (err) {
    // R-643: the body flag is what useSyncHook reads. A cached snapshot is
    // 200 so a failed scan does not paint a gateway error in the console.
    // 4xx still passes through below, and a miss still returns the upstream
    // status. A disconnected browser aborts via request.signal.
    const status = err instanceof RadonApiError ? err.status : 502;
    if (status >= 500) try {
      const cached = await readCachedGex();
      if (cached) {
        const res = NextResponse.json(
          { ...normalizeGexPayload(cached), is_stale: true, scan_succeeded: false },
        );
        res.headers.set("X-Sync-Warning", "GEX sync failed - serving cached data");
        return res;
      }
    } catch {
      // Preserve the upstream failure below.
    }
    const message = scrubSecrets(err instanceof Error ? err.message : "GEX scan failed");
    return NextResponse.json({ error: message, scan_succeeded: false }, { status });
  }
}
