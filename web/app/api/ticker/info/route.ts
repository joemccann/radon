import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { promises as fs } from "fs";
import path from "path";
import {
  getRequestId,
  jsonApiError,
  setCacheResponseHeaders,
} from "@/lib/apiContracts";
import { canReuseUwInfo, hasAnyTickerData, isPopulated, pickUwInfo, stockStateRefreshDue } from "@/lib/tickerInfoCache";
import { enforceDemoAiQuota } from "@/lib/demo/enforceAiQuota";
import { countedUwFetch } from "@/lib/uwCountedFetch";

export const runtime = "nodejs";

/* ─── Types ─── */

type CacheEntry = {
  ticker: string;
  profile_expires: string | null; // null = never expires
  stats_expires: string;
  uw_info: Record<string, unknown>;
  stock_state: Record<string, unknown>;
  exa_profile: Record<string, unknown>;
  exa_stats: Record<string, unknown>;
  // Optional: legacy entries predate the field; the HIT path self-heals them.
  short_float?: Record<string, unknown>;
  // Last float-fetch ATTEMPT. Some tickers structurally have no float rows at
  // UW (indexes, foreign listings) — without this stamp the HIT path would
  // re-fire the float fetch on every request forever for them. Empty + fresh
  // stamp = checked recently, don't retry yet.
  short_float_checked_at?: string;
  // Last stock-state fetch ATTEMPT. HIT path honors a 15-minute TTL so a
  // populated cache does not hit UW stock-state on every request.
  stock_state_checked_at?: string;
  fetched_at: string;
};

const FLOAT_RECHECK_MS = 24 * 60 * 60 * 1000;
const PROVIDER_TIMEOUT_MS = 4_000;

function floatRecheckDue(entry: CacheEntry): boolean {
  if (isPopulated(entry.short_float ?? {})) return false;
  if (!entry.short_float_checked_at) return true;
  return Date.now() - new Date(entry.short_float_checked_at).getTime() >= FLOAT_RECHECK_MS;
}

/* ─── Cache helpers ─── */

function cacheDir(): string {
  return path.resolve(process.cwd(), "..", "data", "company_info_cache");
}

function cachePath(ticker: string): string {
  return path.join(cacheDir(), `${ticker}.json`);
}

function stats24hExpiry(): string {
  return new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
}

async function readCache(ticker: string): Promise<CacheEntry | null> {
  try {
    const raw = await fs.readFile(cachePath(ticker), "utf-8");
    return JSON.parse(raw) as CacheEntry;
  } catch {
    return null;
  }
}

async function writeCache(entry: CacheEntry): Promise<void> {
  try {
    await fs.mkdir(cacheDir(), { recursive: true });
    await fs.writeFile(cachePath(entry.ticker), JSON.stringify(entry, null, 2));
  } catch {
    // non-fatal
  }
}

function isStatsExpired(entry: CacheEntry): boolean {
  return new Date(entry.stats_expires) <= new Date();
}

function hasProfile(entry: CacheEntry): boolean {
  return Object.keys(entry.exa_profile).length > 0;
}


/* ─── Exa parsing ─── */

function parseExaText(text: string): { profile: Record<string, unknown>; stats: Record<string, unknown> } {
  const profile: Record<string, unknown> = {};
  const stats: Record<string, unknown> = {};

  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    // Skip blank lines to find the next non-empty value line
    let nextLine = "";
    for (let j = i + 1; j < lines.length; j++) {
      const candidate = lines[j].trim();
      if (candidate) { nextLine = candidate; break; }
    }

    if (line === "CEO" && nextLine) { profile.ceo = nextLine; continue; }
    if (line === "Employees" && nextLine) { profile.employees = nextLine; continue; }
    if (line === "Headquarters" && nextLine) { profile.headquarters = nextLine; continue; }
    if (line === "Founded" && nextLine) { profile.founded = nextLine; continue; }
    if (line === "Price-Earnings Ratio" && nextLine) { stats.pe_ratio = nextLine; continue; }
    if ((line === "Price-Earnings ratio" || line === "Price-Earnings Ratio") && nextLine) { stats.pe_ratio = nextLine; continue; }
    if ((line === "Dividend Yield" || line === "Dividend yield") && nextLine) { stats.dividend_yield = nextLine; continue; }
    if ((line === "Average Volume" || line === "Average volume") && nextLine) { stats.avg_volume = nextLine; continue; }

    // 52 Week high/low — exact line match only (avoid concatenated duplicates like "52 Week high$207.52")
    if (/^52\s*Week\s*[Hh]igh$/.test(line) && nextLine) {
      const val = nextLine.replace(/[$,]/g, "");
      if (!isNaN(parseFloat(val))) stats.week_52_high = parseFloat(val);
      continue;
    }
    if (/^52\s*Week\s*[Ll]ow$/.test(line) && nextLine) {
      const val = nextLine.replace(/[$,]/g, "");
      if (!isNaN(parseFloat(val))) stats.week_52_low = parseFloat(val);
      continue;
    }
  }

  return { profile, stats };
}

/* ─── UW API helpers ─── */

async function fetchUWStockInfo(ticker: string, token: string): Promise<Record<string, unknown>> {
  try {
    const res = await countedUwFetch(
      `https://api.unusualwhales.com/api/stock/${encodeURIComponent(ticker)}/info`,
      { cache: "no-store", headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(PROVIDER_TIMEOUT_MS) },
    );
    if (!res.ok) return {};
    const json = await res.json();
    return json.data ?? json ?? {};
  } catch {
    return {};
  }
}

/**
 * Public float via UW short-interest history. `data` is an array of dated
 * rows, newest first; the float lives in `total_float` (live-probed
 * 2026-07-21). Failure returns {} — missing float renders "---" downstream.
 */
async function fetchUWShortFloat(ticker: string, token: string): Promise<Record<string, unknown>> {
  try {
    const res = await countedUwFetch(
      `https://api.unusualwhales.com/api/shorts/${encodeURIComponent(ticker)}/interest-float/v2`,
      { cache: "no-store", headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(PROVIDER_TIMEOUT_MS) },
    );
    if (!res.ok) return {};
    const json = await res.json();
    if (Array.isArray(json.data)) return (json.data[0] ?? {}) as Record<string, unknown>;
    return (json.data ?? json ?? {}) as Record<string, unknown>;
  } catch {
    return {};
  }
}

async function fetchUWStockState(ticker: string, token: string): Promise<Record<string, unknown>> {
  try {
    const res = await countedUwFetch(
      `https://api.unusualwhales.com/api/stock/${encodeURIComponent(ticker)}/stock-state`,
      { cache: "no-store", headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(PROVIDER_TIMEOUT_MS) },
    );
    if (!res.ok) return {};
    const json = await res.json();
    return json.data ?? json ?? {};
  } catch {
    return {};
  }
}

/* ─── Exa API ─── */

async function fetchExaData(ticker: string): Promise<{ profile: Record<string, unknown>; stats: Record<string, unknown> }> {
  const apiKey = process.env.EXA_API_KEY;
  if (!apiKey) return { profile: {}, stats: {} };

  try {
    const res = await fetch("https://api.exa.ai/search", {
      method: "POST",
      cache: "no-store",
      headers: { "x-api-key": apiKey, "Content-Type": "application/json" },
      body: JSON.stringify({
        query: `${ticker} stock key statistics`,
        numResults: 1,
        type: "auto",
        contents: { text: { maxCharacters: 3000 } },
        includeDomains: ["robinhood.com"],
      }),
      signal: AbortSignal.timeout(PROVIDER_TIMEOUT_MS),
    });

    if (!res.ok) return { profile: {}, stats: {} };
    const json = await res.json();
    const text = json.results?.[0]?.text;
    if (!text) return { profile: {}, stats: {} };

    return parseExaText(text);
  } catch {
    return { profile: {}, stats: {} };
  }
}

/* ─── Yahoo Finance fallback (52W, no auth required) ─── */

async function fetchYahooStats(ticker: string): Promise<Record<string, unknown>> {
  try {
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=1d&interval=1d`;
    const res = await fetch(url, {
      cache: "no-store",
      headers: { "User-Agent": "Mozilla/5.0" },
      signal: AbortSignal.timeout(PROVIDER_TIMEOUT_MS),
    });
    if (!res.ok) return {};
    const json = await res.json();
    const meta = json?.chart?.result?.[0]?.meta;
    if (!meta) return {};

    const stats: Record<string, unknown> = {};
    if (typeof meta.fiftyTwoWeekHigh === "number") stats.week_52_high = meta.fiftyTwoWeekHigh;
    if (typeof meta.fiftyTwoWeekLow === "number") stats.week_52_low = meta.fiftyTwoWeekLow;
    return stats;
  } catch {
    return {};
  }
}

const CACHE_TTL_SECONDS = 20;

/* ─── Route handler ─── */

export const radonCapability = "read";

export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "ticker/info:route", limit: 20, windowMs: 60_000 } });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const { searchParams } = new URL(request.url);
  const ticker = searchParams.get("ticker");

  if (!ticker) {
    return jsonApiError({
      message: "ticker parameter required",
      status: 400,
      code: "BAD_REQUEST",
      requestId,
    });
  }

  const symbol = ticker.toUpperCase();

  // Defense-in-depth path-traversal guard before the symbol reaches a cache
  // file path (mirrors flow-analysis route).
  if (!/^[A-Z0-9.]{1,10}$/.test(symbol)) {
    return jsonApiError({
      message: "invalid ticker",
      status: 400,
      code: "BAD_REQUEST",
      requestId,
    });
  }

  const token = process.env.UW_TOKEN;

  // 1. Check cache
  const cached = await readCache(symbol);
  const profileCached = cached && hasProfile(cached);
  const statsCached = cached && !isStatsExpired(cached);

  // If profile + stats + uw_info are all cached, refresh stock-state only
  // when its 15-minute TTL has expired. uw_info MUST be non-empty to take
  // this fast path — a prior transient UW failure leaves uw_info: {} which
  // would otherwise re-serve an empty market cap / beta for the full 24h
  // stats window (see feedback_dont_cache_empty_results).
  if (profileCached && statsCached && isPopulated(cached?.uw_info)) {
    let stockState = cached.stock_state;
    let shortFloat = cached.short_float ?? {};
    if (token) {
      // Legacy entries (and past float failures) lack short_float — self-heal
      // at most once per recheck window. Stock-state is gated by its own
      // 15-minute TTL so a HIT does not call UW on every request.
      const refreshState = stockStateRefreshDue(cached);
      const refreshFloat = floatRecheckDue(cached);
      if (refreshState || refreshFloat) {
        const [freshState, freshFloat] = await Promise.all([
          refreshState ? fetchUWStockState(symbol, token) : Promise.resolve(null),
          refreshFloat ? fetchUWShortFloat(symbol, token) : Promise.resolve(null),
        ]);
        let cacheDirty = false;
        if (refreshState) {
          cached.stock_state_checked_at = new Date().toISOString();
          cacheDirty = true;
          if (freshState && Object.keys(freshState).length > 0) {
            stockState = freshState;
            cached.stock_state = freshState;
          }
        }
        if (freshFloat != null) {
          cached.short_float_checked_at = new Date().toISOString();
          cacheDirty = true;
          if (isPopulated(freshFloat)) {
            shortFloat = freshFloat;
            cached.short_float = freshFloat;
          }
        }
        if (cacheDirty) await writeCache(cached);
      }
    }

    const response = NextResponse.json({
      uw_info: cached.uw_info,
      stock_state: stockState,
      profile: cached.exa_profile,
      stats: cached.exa_stats,
      short_float: shortFloat,
    });
    return setCacheResponseHeaders(response, {
      maxAgeSeconds: CACHE_TTL_SECONDS,
      staleWhileRevalidateSeconds: 60,
      requestId,
      cacheState: "HIT",
      tags: [`ticker-info.${symbol}`],
    });
  }

  if (!token) {
    return jsonApiError({
      message: "UW_TOKEN not configured",
      status: 500,
      code: "CONFIG_ERROR",
      requestId,
    });
  }

  // Demo AI quota (Phase 4) — charged only on a cache MISS (the expensive
  // UW/Exa/Yahoo fan-out below); no-op for non-demo users.
  const quota = await enforceDemoAiQuota("ticker/info");
  if (quota) return quota;

  try {
    // 2. Fetch UW data (always fresh for stock-state).
    // Reuse cached uw_info only when it actually carries data — never let an
    // empty {} from a past failure short-circuit the re-fetch behind the 24h
    // stats TTL.
    const reuseUwInfo = canReuseUwInfo(cached?.uw_info, !!statsCached);
    const [fetchedUwInfo, stockState, fetchedShortFloat] = await Promise.all([
      reuseUwInfo && cached ? Promise.resolve(cached.uw_info) : fetchUWStockInfo(symbol, token),
      fetchUWStockState(symbol, token),
      fetchUWShortFloat(symbol, token),
    ]);
    // Float changes slowly (biweekly settlement data) — a transient failure
    // falls back to the last-good cached value rather than blanking it.
    const shortFloat = isPopulated(fetchedShortFloat)
      ? fetchedShortFloat
      : (cached?.short_float ?? {});
    // Don't downgrade a populated cache to empty on a transient UW failure:
    // prefer the fresh fetch, fall back to the last-good cached value.
    const uwInfo = pickUwInfo(fetchedUwInfo, cached?.uw_info);

    // 3. Fetch Exa data if needed, with Yahoo Finance fallback for 52W stats
    let exaProfile: Record<string, unknown> = cached?.exa_profile ?? {};
    let exaStats: Record<string, unknown> = cached?.exa_stats ?? {};

    if (!profileCached || !statsCached) {
      const exa = await fetchExaData(symbol);
      if (!profileCached && Object.keys(exa.profile).length > 0) {
        exaProfile = exa.profile;
      }
      if (!statsCached && Object.keys(exa.stats).length > 0) {
        exaStats = exa.stats;
      }

      // Yahoo Finance fallback: fill 52W gaps when Exa didn't provide them
      if (!exaStats.week_52_high || !exaStats.week_52_low) {
        const yahoo = await fetchYahooStats(symbol);
        if (!exaStats.week_52_high && yahoo.week_52_high) exaStats.week_52_high = yahoo.week_52_high;
        if (!exaStats.week_52_low && yahoo.week_52_low) exaStats.week_52_low = yahoo.week_52_low;
      }
    }

    // 4. Write cache. Never persist an all-empty payload behind the 24h stats
    // TTL — that is exactly the poisoning that strands a ticker with "---"
    // until the window expires. Skipping the write makes the next request
    // retry immediately.
    const hasAnyData = hasAnyTickerData(uwInfo, exaProfile, exaStats);
    const entry: CacheEntry = {
      ticker: symbol,
      profile_expires: null, // never
      stats_expires: stats24hExpiry(),
      uw_info: uwInfo,
      stock_state: stockState,
      exa_profile: exaProfile,
      exa_stats: exaStats,
      short_float: shortFloat,
      short_float_checked_at: new Date().toISOString(),
      stock_state_checked_at: new Date().toISOString(),
      fetched_at: new Date().toISOString(),
    };
    if (hasAnyData) await writeCache(entry);

    const response = NextResponse.json({
      uw_info: uwInfo,
      stock_state: stockState,
      profile: exaProfile,
      stats: exaStats,
      short_float: shortFloat,
    });
    return setCacheResponseHeaders(response, {
      maxAgeSeconds: CACHE_TTL_SECONDS,
      staleWhileRevalidateSeconds: 60,
      requestId,
      cacheState: cached ? "STALE" : "MISS",
      tags: [`ticker-info.${symbol}`],
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to fetch company info";
    return jsonApiError({
      message,
      status: 500,
      code: "UPSTREAM_ERROR",
      detail: "ticker-info failed",
      requestId,
    });
  }
}
