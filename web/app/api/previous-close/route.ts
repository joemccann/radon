import { NextResponse } from "next/server";
import { WebSocket } from "ws";

/**
 * Fetch previous-day closing prices for stock symbols.
 * Priority: IB (via WS snapshot) → UW → Yahoo Finance.
 *
 * POST { symbols: ["ILF", "TSLL"] }
 * => { closes: { "ILF": 34.56, "TSLL": 14.89 } }
 */

// In-memory cache keyed by "SYMBOL:YYYY-MM-DD" — previous close doesn't change within a day
const cache = new Map<string, number>();

function cacheKey(symbol: string): string {
  return `${symbol}:${new Date().toISOString().slice(0, 10)}`;
}

/* ── IB source (via WebSocket snapshot) ─────────────────── */

const IB_WS_URL = process.env.IB_REALTIME_WS_URL || "ws://localhost:8765";

/**
 * Batch-fetch previous close from the IB realtime server.
 * Sends a snapshot request for all symbols and collects `close` fields.
 * Returns a map of symbol → close for symbols that had data.
 */
async function fetchFromIB(symbols: string[]): Promise<Record<string, number>> {
  const results: Record<string, number> = {};
  if (symbols.length === 0) return results;

  return new Promise<Record<string, number>>((resolve) => {
    let ws: WebSocket;
    const pending = new Set(symbols);
    const timeout = setTimeout(() => {
      try { ws?.close(); } catch { /* ignore */ }
      resolve(results);
    }, 3000);

    try {
      ws = new WebSocket(IB_WS_URL);
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
  try {
    const { symbols } = await req.json();
    if (!Array.isArray(symbols) || symbols.length === 0) {
      return NextResponse.json({ closes: {} });
    }

    const batch = symbols.slice(0, 30).map((s: string) => s.toUpperCase());

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
    const ibResults = await fetchFromIB(uncached);
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
