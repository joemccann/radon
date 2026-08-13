import { requireRouteAccess } from "@/lib/routeAccess";
import { boundedUniqueTickers } from "@/lib/requestBounds";

import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { WebSocket } from "ws";
import { isUsTradingDay } from "@/lib/serviceHealthWindows";

/**
 * Fetch previous-day closing prices for stock symbols.
 * Priority: IB (via WS snapshot) → UW → Yahoo Finance.
 *
 * POST { symbols: ["ILF", "TSLL"] }
 * => { closes: { "ILF": 34.56, "TSLL": 14.89 } }
 */

// In-memory cache keyed by "SYMBOL:YYYY-MM-DD" — previous close doesn't change within a day
const cache = new Map<string, number>();

function etDateString(now: Date): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

export function previousCloseSessionDate(now: Date = new Date()): string {
  const today = etDateString(now);
  const cursor = new Date(`${today}T12:00:00Z`);
  do cursor.setUTCDate(cursor.getUTCDate() - 1);
  while (!isUsTradingDay(cursor.toISOString().slice(0, 10)));
  return cursor.toISOString().slice(0, 10);
}

function cacheKey(symbol: string, now: Date = new Date()): string {
  return `${symbol}:${previousCloseSessionDate(now)}`;
}

/* ── IB source (via WebSocket snapshot) ─────────────────── */

const IB_WS_URL = process.env.IB_REALTIME_WS_URL || "ws://localhost:8765";
const RADON_API = process.env.RADON_API_URL || "http://localhost:8321";

async function buildWsUrl(token: string | null): Promise<string> {
  if (!token) return IB_WS_URL;
  try {
    const res = await fetch(`${RADON_API}/ws-ticket`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      signal: AbortSignal.timeout(750),
    });
    if (!res.ok) return IB_WS_URL;
    const { ticket } = await res.json();
    const sep = IB_WS_URL.includes("?") ? "&" : "?";
    return `${IB_WS_URL}${sep}ticket=${ticket}`;
  } catch {
    return IB_WS_URL;
  }
}

/**
 * Batch-fetch previous close from the IB realtime server.
 * Sends a snapshot request for all symbols and collects `close` fields.
 * Returns a map of symbol → close for symbols that had data.
 */
async function fetchFromIB(symbols: string[], token: string | null): Promise<Record<string, number>> {
  const results: Record<string, number> = {};
  if (symbols.length === 0) return results;

  const wsUrl = await buildWsUrl(token);

  return new Promise<Record<string, number>>((resolve) => {
    let ws: WebSocket;
    const pending = new Set(symbols);
    const timeout = setTimeout(() => {
      try { ws?.close(); } catch { /* ignore */ }
      resolve(results);
    }, 3000);

    try {
      ws = new WebSocket(wsUrl);
    } catch {
      clearTimeout(timeout);
      resolve(results);
      return;
    }

    ws.on("error", () => {
      clearTimeout(timeout);
      try { ws.close(); } catch { /* ignore */ }
      resolve(results);
    });

    ws.on("open", () => {
      // Request snapshot for each symbol
      try {
        ws.send(JSON.stringify({ action: "snapshot", symbols }));
      } catch {
        clearTimeout(timeout);
        try { ws.close(); } catch { /* ignore */ }
        resolve(results);
      }
    });

    ws.on("message", (raw: Buffer | string) => {
      try {
        const msg = JSON.parse(typeof raw === "string" ? raw : raw.toString());
        if (msg.type === "snapshot" && msg.symbol && msg.data) {
          const close = msg.data.close;
          if (typeof close === "number" && close > 0) {
            results[msg.symbol] = close;
          }
          pending.delete(msg.symbol);
        }
        // Resolve early once all snapshots received
        if (pending.size === 0) {
          clearTimeout(timeout);
          try { ws.close(); } catch { /* ignore */ }
          resolve(results);
        }
      } catch {
        // Ignore parse errors
      }
    });

    ws.on("close", () => {
      clearTimeout(timeout);
      resolve(results);
    });
  });
}

/* ── UW source ──────────────────────────────────────────── */

async function fetchFromUW(symbol: string): Promise<number | null> {
  const token = process.env.UW_TOKEN;
  if (!token) return null;
  try {
    const res = await fetch(
      `https://api.unusualwhales.com/api/stock/${encodeURIComponent(symbol)}/quote`,
      {
        headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
        signal: AbortSignal.timeout(5000),
      },
    );
    if (!res.ok) return null;
    const data = await res.json();
    // UW quote response shape varies — try common field names
    const prev =
      data?.data?.previous_close ??
      data?.data?.prev_close ??
      data?.previous_close ??
      data?.prev_close;
    if (typeof prev === "number" && prev > 0) return prev;
    return null;
  } catch {
    return null;
  }
}

/* ── Yahoo Finance source ───────────────────────────────── */

/**
 * "YYYY-MM-DD" formatted in America/New_York. Used to filter Yahoo's
 * daily close array against today (ET) — a close timestamped *today*
 * is the current session, not yesterday's close. Comparing date strings
 * lexicographically is safe because the format is zero-padded.
 */
function unixSecondsToEtDateString(seconds: number): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(seconds * 1000));
}

/**
 * Walk Yahoo's daily close array newest → oldest and return the most
 * recent valid close whose ET date is strictly before today. Handles
 * gappy series (null cells) by continuing the walk.
 */
function lastClosePriorToTodayEt(
  timestamps: unknown,
  closes: unknown,
): number | null {
  if (!Array.isArray(timestamps) || !Array.isArray(closes)) return null;
  if (timestamps.length !== closes.length) return null;
  const expectedSession = previousCloseSessionDate();
  for (let i = closes.length - 1; i >= 0; i--) {
    const t = timestamps[i];
    const c = closes[i];
    if (typeof t !== "number" || typeof c !== "number" || !(c > 0)) continue;
    if (unixSecondsToEtDateString(t) <= expectedSession) return c;
  }
  return null;
}

/**
 * Read yesterday's close from Yahoo Finance.
 *
 * Yahoo's chart endpoint exposes three meta fields that look superficially
 * like "previous close" but only one of them is. `meta.chartPreviousClose`
 * is the close on the day immediately BEFORE the requested range starts —
 * for `range=5d` that is ~6 trading days ago, not yesterday. Preferring it
 * silently produced wildly wrong Day Chg % for any symbol whose IB CLOSE
 * tick was missing (NAK / RR / MSFT, observed 2026-05-20).
 *
 * Correct read order:
 *   1. `indicators.quote[0].close[]` walked back from the newest entry
 *      whose timestamp lands on an ET date earlier than today.
 *   2. `meta.regularMarketPreviousClose` as a fallback when the daily
 *      array is absent (rare).
 *   3. Nothing else — return null and let the UI show "---" rather than
 *      a confidently wrong number.
 */
async function fetchFromYahoo(symbol: string): Promise<number | null> {
  try {
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=5d&interval=1d`;
    const res = await fetch(url, {
      headers: { "User-Agent": "Mozilla/5.0" },
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return null;
    const data = await res.json();
    const result = data?.chart?.result?.[0];
    if (!result) return null;

    const dailyClose = lastClosePriorToTodayEt(
      result.timestamp,
      result.indicators?.quote?.[0]?.close,
    );
    if (dailyClose != null) return dailyClose;

    const regularPrev = result.meta?.regularMarketPreviousClose;
    if (typeof regularPrev === "number" && regularPrev > 0) return regularPrev;

    return null;
  } catch {
    return null;
  }
}

/* ── Combined fetcher with cache ────────────────────────── */

async function getPreviousClose(symbol: string): Promise<number | null> {
  const key = cacheKey(symbol);
  if (cache.has(key)) return cache.get(key)!;

  // IB is tried in batch before this function — skip individual IB calls.
  // Try UW, then Yahoo as last resort.
  let close = await fetchFromUW(symbol);
  if (close == null) {
    close = await fetchFromYahoo(symbol);
  }

  if (close != null) {
    cache.set(key, close);
  }
  return close;
}

/* ── Route handler ──────────────────────────────────────── */

export async function POST(req: Request) {
  const access = await requireRouteAccess(undefined, { rate: { key: "previous-close:route", limit: 20, windowMs: 60_000 } });
  if (!access.ok) return access.response;
  try {
    const { symbols } = await req.json();
    const batch = boundedUniqueTickers(symbols, 30);
    if (!batch) return NextResponse.json({ error: "symbols must contain 1 to 30 valid unique tickers" }, { status: 400 });

    const { getToken } = await auth();
    const token = await getToken();

    // Check cache first, collect uncached symbols
    const closes: Record<string, number> = {};
    const uncached: string[] = [];
    for (const sym of batch) {
      const key = cacheKey(sym);
      if (cache.has(key)) {
        closes[sym] = cache.get(key)!;
      } else {
        uncached.push(sym);
      }
    }

    if (uncached.length === 0) {
      return NextResponse.json({ closes });
    }

    // 1st priority: IB (batch snapshot via WebSocket)
    const ibResults = await fetchFromIB(uncached, token);
    const stillMissing: string[] = [];
    for (const sym of uncached) {
      if (ibResults[sym] != null) {
        closes[sym] = ibResults[sym];
        cache.set(cacheKey(sym), ibResults[sym]);
      } else {
        stillMissing.push(sym);
      }
    }

    // 2nd/3rd priority: UW → Yahoo for symbols IB didn't return
    if (stillMissing.length > 0) {
      const fallbackResults = await Promise.all(
        stillMissing.map(async (sym) => {
          const close = await getPreviousClose(sym);
          return [sym, close] as const;
        }),
      );
      for (const [sym, close] of fallbackResults) {
        if (close != null) closes[sym] = close;
      }
    }

    return NextResponse.json({ closes });
  } catch {
    return NextResponse.json({ closes: {} }, { status: 500 });
  }
}
