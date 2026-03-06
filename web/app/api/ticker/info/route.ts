import { NextResponse } from "next/server";
import { promises as fs } from "fs";
import path from "path";

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
  fetched_at: string;
};

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
    const nextLine = (i + 1 < lines.length) ? lines[i + 1]?.trim() : "";

    if (line === "CEO" && nextLine) { profile.ceo = nextLine; continue; }
    if (line === "Employees" && nextLine) { profile.employees = nextLine; continue; }
    if (line === "Headquarters" && nextLine) { profile.headquarters = nextLine; continue; }
    if (line === "Founded" && nextLine) { profile.founded = nextLine; continue; }
    if (line === "Price-Earnings Ratio" && nextLine) { stats.pe_ratio = nextLine; continue; }
    if ((line === "Price-Earnings ratio" || line === "Price-Earnings Ratio") && nextLine) { stats.pe_ratio = nextLine; continue; }
    if (line === "Dividend Yield" && nextLine) { stats.dividend_yield = nextLine; continue; }
    if (line === "Average Volume" && nextLine) { stats.avg_volume = nextLine; continue; }

    // 52 Week high/low
    const high52 = line.match(/52\s*Week\s*[Hh]igh/);
    if (high52 && nextLine) {
      const val = nextLine.replace(/[$,]/g, "");
      if (!isNaN(parseFloat(val))) stats.week_52_high = parseFloat(val);
      continue;
    }
    const low52 = line.match(/52\s*Week\s*[Ll]ow/);
    if (low52 && nextLine) {
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
    const res = await fetch(
      `https://api.unusualwhales.com/api/stock/${encodeURIComponent(ticker)}/info`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!res.ok) return {};
    const json = await res.json();
    return json.data ?? json ?? {};
  } catch {
    return {};
  }
}

async function fetchUWStockState(ticker: string, token: string): Promise<Record<string, unknown>> {
  try {
    const res = await fetch(
      `https://api.unusualwhales.com/api/stock/${encodeURIComponent(ticker)}/stock-state`,
      { headers: { Authorization: `Bearer ${token}` } },
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
      headers: { "x-api-key": apiKey, "Content-Type": "application/json" },
      body: JSON.stringify({
        query: `${ticker} stock key statistics`,
        numResults: 1,
        type: "auto",
        contents: { text: { maxCharacters: 3000 } },
        includeDomains: ["robinhood.com"],
      }),
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

/* ─── Route handler ─── */

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url);
  const ticker = searchParams.get("ticker");

  if (!ticker) {
    return NextResponse.json({ error: "ticker parameter required" }, { status: 400 });
  }

  const symbol = ticker.toUpperCase();
  const token = process.env.UW_TOKEN;

  // 1. Check cache
  const cached = await readCache(symbol);
  const profileCached = cached && hasProfile(cached);
  const statsCached = cached && !isStatsExpired(cached);

  // If both profile and stats are cached, just refresh stock-state (intraday)
  if (profileCached && statsCached) {
    let stockState = cached.stock_state;
    if (token) {
      const freshState = await fetchUWStockState(symbol, token);
      if (Object.keys(freshState).length > 0) {
        stockState = freshState;
        // Update cache with fresh stock-state
        cached.stock_state = freshState;
        await writeCache(cached);
      }
    }
    return NextResponse.json({
      uw_info: cached.uw_info,
      stock_state: stockState,
      profile: cached.exa_profile,
      stats: cached.exa_stats,
    });
  }

  if (!token) {
    return NextResponse.json({ error: "UW_TOKEN not configured" }, { status: 500 });
  }

  try {
    // 2. Fetch UW data (always fresh for stock-state)
    const [uwInfo, stockState] = await Promise.all([
      statsCached && cached ? Promise.resolve(cached.uw_info) : fetchUWStockInfo(symbol, token),
      fetchUWStockState(symbol, token),
    ]);

    // 3. Fetch Exa data if needed
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
    }

    // 4. Write cache
    const entry: CacheEntry = {
      ticker: symbol,
      profile_expires: null, // never
      stats_expires: stats24hExpiry(),
      uw_info: uwInfo,
      stock_state: stockState,
      exa_profile: exaProfile,
      exa_stats: exaStats,
      fetched_at: new Date().toISOString(),
    };
    await writeCache(entry);

    return NextResponse.json({
      uw_info: uwInfo,
      stock_state: stockState,
      profile: exaProfile,
      stats: exaStats,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to fetch company info";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
