import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { isVcgDataStale } from "@/lib/vcgStaleness";
import { radonFetch } from "@/lib/radonApi";
import { createBackgroundScanTrigger } from "@/lib/backgroundScan";
import { getRequestId, setCacheResponseHeaders, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { getDb } from "@/lib/db";
import { cachedRead } from "@/lib/dbCache";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { buildDemoVcgFixture } from "@/lib/demo/fixtures/regime";
import { isMarketOpenAt, mostRecentSessionDate } from "@/lib/marketCalendar";
// Disable Next.js static caching: this handler reads live Turso state. Without
// this, the framework freezes the first response and serves stale data until
// the dev server restarts.
export const dynamic = "force-dynamic";

export const runtime = "nodejs";


const EMPTY_VCG = {
  scan_time: "",
  market_open: false,
  credit_proxy: "HYG",
  signal: {
    vcg: null, vcg_adj: null, residual: null,
    beta1_vvix: null, beta2_vix: null, alpha: null,
    vix: 0, vvix: 0, credit_price: 0, credit_5d_return_pct: 0,
    ro: 0, edr: 0, tier: null, bounce: 0,
    vvix_severity: "moderate",
    sign_ok: true, sign_suppressed: false, pi_panic: 0,
    regime: "DIVERGENCE",
    interpretation: "NORMAL",
    attribution: { vvix_pct: 0, vix_pct: 0, vvix_component: 0, vix_component: 0, model_implied: 0 },
  },
  history: [],
};

export function expectedVcgSessionDate(now = new Date()): string {
  return mostRecentSessionDate(now);
}

// Mirrors the intraday scan_time TTL in vcgStaleness.ts.
const VCG_MAX_AGE_MS = 60_000;

async function readVcgFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const db = getDb();
  const result = await db.execute({
    sql: `SELECT scan_time, payload FROM vcg_snapshots ORDER BY scan_time DESC LIMIT 1`,
    args: [],
  });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { scan_time: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.scan_time),
  };
}

// Coalesces every client tab's ~60s poll into one source read per window
// (contract: tests/db-read-cache-contract.test.ts).
const READ_CACHE_TTL_MS = 5_000;

/** Latest Turso row, or the last good read held in memory when Turso fails. */
async function readCachedVcg(): Promise<Record<string, unknown> | null> {
  const result = await cachedRead("vcg:snapshot", READ_CACHE_TTL_MS, () =>
    dbFirstRead({
      fromDb: readVcgFromDb,
      maxAgeMs: VCG_MAX_AGE_MS,
      label: "vcg",
    }),
  );
  return result.ok ? result.data : null;
}

function normalizeVcgPayload(raw: Record<string, unknown>): Record<string, unknown> {
  const signal = (raw.signal as Record<string, unknown>) ?? {};
  const attr = (signal.attribution as Record<string, unknown>) ?? {};

  return {
    ...EMPTY_VCG,
    scan_time: typeof raw.scan_time === "string" ? raw.scan_time : "",
    market_open: typeof raw.market_open === "boolean" ? raw.market_open : isMarketOpenAt(),
    credit_proxy: typeof raw.credit_proxy === "string" ? raw.credit_proxy : "HYG",
    signal: {
      ...EMPTY_VCG.signal,
      ...signal,
      attribution: { ...EMPTY_VCG.signal.attribution, ...attr },
    },
    history: Array.isArray(raw.history) ? raw.history : [],
  };
}

const triggerBackgroundScan = createBackgroundScanTrigger({
  label: "VCG",
  run: () => radonFetch<Record<string, unknown>>("/vcg/scan", { method: "POST", timeout: 130_000 }),
});

export const radonCapability = "read";

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "vcg:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  if (access.principal.kind === "demo") {
    return setNoStoreResponseHeaders(NextResponse.json(buildDemoVcgFixture()), requestId);
  }
  const cached = await readCachedVcg();
  const data = normalizeVcgPayload(cached ?? {});
  const currentMarketOpen = isMarketOpenAt();

  (data as Record<string, unknown>).market_open = currentMarketOpen;

  if (!cached) {
    // No Turso row and no remembered read. EMPTY_VCG's
    // defaults are an affirmative reading — regime "DIVERGENCE",
    // interpretation "NORMAL", ro/edr 0 which suppress the RISK-OFF and EDR
    // pills — so a dead feed rendered as "no risk-off signal". R-228.
    const degraded = data as Record<string, unknown>;
    degraded.missing = true;
    degraded.signal = null;
    triggerBackgroundScan();
    const missingResponse = NextResponse.json(degraded);
    missingResponse.headers.set("X-Sync-Warning", "vcg-source-unavailable");
    return setCacheResponseHeaders(missingResponse, {
      maxAgeSeconds: 0,
      staleWhileRevalidateSeconds: 0,
      requestId,
      cacheState: "MISS",
      tags: ["vcg"],
    });
  }

  // Stale-while-revalidate. `cached` keeps its own scan_time when it came from
  // memory, so an outage reads stale here and still kicks the background rescan.
  const stale = isVcgDataStale(
    cached as { scan_time?: string; market_open?: boolean },
    expectedVcgSessionDate(),
    currentMarketOpen,
  );

  if (stale) {
    triggerBackgroundScan();
  }

  const response = NextResponse.json(data);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 15,
    staleWhileRevalidateSeconds: 120,
    requestId,
    cacheState: "HIT",
    tags: ["vcg"],
  });
}
