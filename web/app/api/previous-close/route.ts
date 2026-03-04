import { NextResponse } from "next/server";

/**
 * Fetch previous-day closing prices for stock symbols.
 * Tries UW first (if UW_TOKEN set), falls back to Yahoo Finance.
 *
 * POST { symbols: ["ILF", "TSLL"] }
 * => { closes: { "ILF": 34.56, "TSLL": 14.89 } }
 */

// In-memory cache keyed by "SYMBOL:YYYY-MM-DD" — previous close doesn't change within a day
const cache = new Map<string, number>();

function cacheKey(symbol: string): string {
  return `${symbol}:${new Date().toISOString().slice(0, 10)}`;
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

async function fetchFromYahoo(symbol: string): Promise<number | null> {
  try {
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=5d&interval=1d`;
    const res = await fetch(url, {
      headers: { "User-Agent": "Mozilla/5.0" },
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return null;
    const data = await res.json();
    const meta = data?.chart?.result?.[0]?.meta;
    const prev =
      meta?.chartPreviousClose ??
      meta?.previousClose ??
      meta?.regularMarketPreviousClose;
    if (typeof prev === "number" && prev > 0) return prev;
    return null;
  } catch {
    return null;
  }
}

/* ── Combined fetcher with cache ────────────────────────── */

async function getPreviousClose(symbol: string): Promise<number | null> {
  const key = cacheKey(symbol);
  if (cache.has(key)) return cache.get(key)!;

  // Try UW first, then Yahoo
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
  try {
    const { symbols } = await req.json();
    if (!Array.isArray(symbols) || symbols.length === 0) {
      return NextResponse.json({ closes: {} });
    }

    const batch = symbols.slice(0, 30);
    const results = await Promise.all(
      batch.map(async (sym: string) => {
        const close = await getPreviousClose(sym.toUpperCase());
        return [sym, close] as const;
      }),
    );

    const closes: Record<string, number> = {};
    for (const [sym, close] of results) {
      if (close != null) closes[sym] = close;
    }

    return NextResponse.json({ closes });
  } catch {
    return NextResponse.json({ closes: {} }, { status: 500 });
  }
}
