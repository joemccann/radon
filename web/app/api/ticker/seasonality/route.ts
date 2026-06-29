import { NextResponse } from "next/server";
import { promises as fs } from "fs";
import path from "path";
import {
  getRequestId,
  jsonApiError,
  setCacheResponseHeaders,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { enforceDemoAiQuota } from "@/lib/demo/enforceAiQuota";

export const runtime = "nodejs";

type MonthData = {
  month: number;
  avg_change: number;
  median_change: number;
  max_change: number;
  min_change: number;
  positive_months_perc: number;
  years: number;
};

type CacheEntry = {
  ticker: string;
  expires: string;
  source: "uw" | "uw+equityclock" | "equityclock";
  data: MonthData[];
};

const ANTHROPIC_ENV_KEYS = ["ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY", "CLAUDE_API_KEY"];
const resolveApiKey = () => {
  for (const key of ANTHROPIC_ENV_KEYS) {
    const value = process.env[key]?.trim();
    if (value) return value;
  }
  return undefined;
};

/** Cache dir: data/seasonality_cache/ relative to project root */
function cacheDir(): string {
  return path.resolve(process.cwd(), "..", "data", "seasonality_cache");
}

function cachePath(ticker: string): string {
  return path.join(cacheDir(), `${ticker}.json`);
}

/** Expiry = 1st of next month, midnight UTC */
function cacheExpiry(): string {
  const now = new Date();
  const nextMonth = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1));
  return nextMonth.toISOString();
}

async function readCache(ticker: string): Promise<CacheEntry | null> {
  try {
    const raw = await fs.readFile(cachePath(ticker), "utf-8");
    const entry: CacheEntry = JSON.parse(raw);
    if (new Date(entry.expires) > new Date()) return entry;
  } catch {
    // cache miss or parse error
  }
  return null;
}

async function writeCache(entry: CacheEntry): Promise<void> {
  try {
    await fs.mkdir(cacheDir(), { recursive: true });
    await fs.writeFile(cachePath(entry.ticker), JSON.stringify(entry, null, 2));
  } catch {
    // non-fatal — cache write failure shouldn't block response
  }
}

const EXTRACTION_PROMPT = `Extract monthly seasonality data from this table image.
Return ONLY a JSON array of 12 objects (Jan=1 through Dec=12):
[{"month":1,"avg_change":0.053,"median_change":0.02,"max_change":0.15,"min_change":-0.08,"positive_months_perc":0.65,"years":20},...]

Rules:
- All percentages as decimals (5.3% = 0.053, 65% = 0.65)
- month: 1-12
- positive_months_perc = win rate
- years = sample size
- Return ONLY the JSON array, no markdown, no explanation`;

async function extractViaVision(ticker: string, requestId: string): Promise<MonthData[] | null> {
  const apiKey = resolveApiKey();
  if (!apiKey) return null;

  const imageUrl = `https://charts.equityclock.com/seasonal_charts/${encodeURIComponent(ticker)}_sheet.png`;

  // Verify the image exists before sending to Vision
  try {
    const headRes = await fetch(imageUrl, { method: "HEAD", cache: "no-store" });
    if (!headRes.ok) return null;
  } catch {
    return null;
  }

  try {
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      cache: "no-store",
      headers: {
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 1024,
        messages: [
          {
            role: "user",
            content: [
              {
                type: "image",
                source: { type: "url", url: imageUrl },
              },
              { type: "text", text: EXTRACTION_PROMPT },
            ],
          },
        ],
      }),
    });

    if (!res.ok) return null;

    const json = await res.json();
    const text = json.content?.find((b: { type: string; text?: string }) => b.type === "text")?.text;
    if (!text) return null;

    // Strip markdown fences if present (```json ... ```)
    const cleaned = text.replace(/^```(?:json)?\s*/i, "").replace(/\s*```\s*$/, "").trim();

    const parsed = JSON.parse(cleaned);
    if (!Array.isArray(parsed) || parsed.length !== 12) return null;

    return parsed as MonthData[];
  } catch {
    console.warn("[ticker/seasonality] Vision extraction failed", { ticker, requestId });
    return null;
  }
}

const SEASONALITY_CACHE_SECONDS = 900;
const STALE_REVALIDATE_SECONDS = 3600;

export async function GET(request: Request): Promise<Response> {
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

  // Reject anything that isn't a plain ticker before it reaches a cache file
  // path (defense-in-depth path-traversal guard; mirrors flow-analysis route).
  if (!/^[A-Z0-9.]{1,10}$/.test(symbol)) {
    return jsonApiError({
      message: "invalid ticker",
      status: 400,
      code: "BAD_REQUEST",
      requestId,
    });
  }

  // 1. Check cache
  const cached = await readCache(symbol);
  if (cached) {
    const response = NextResponse.json({ data: cached.data, source: cached.source, requestId });
    return setCacheResponseHeaders(response, {
      maxAgeSeconds: SEASONALITY_CACHE_SECONDS,
      staleWhileRevalidateSeconds: STALE_REVALIDATE_SECONDS,
      requestId,
      cacheState: "HIT",
      tags: [`ticker-seasonality.${symbol}`],
    });
  }

  // Demo AI quota (Phase 4) — charged only on a cache MISS (real LLM work);
  // no-op for non-demo users.
  const quota = await enforceDemoAiQuota("ticker/seasonality");
  if (quota) return quota;

  const token = process.env.UW_TOKEN;
  if (!token) {
    return jsonApiError({
      message: "UW_TOKEN not configured",
      status: 500,
      code: "CONFIG_ERROR",
      requestId,
    });
  }

  try {
    // 2. Fetch UW API
    const res = await fetch(
      `https://api.unusualwhales.com/api/seasonality/${encodeURIComponent(symbol)}/monthly`,
      { cache: "no-store", headers: { Authorization: `Bearer ${token}` } },
    );

    let uwData: MonthData[] = [];
    if (res.ok) {
      const json = await res.json();
      uwData = json.data ?? json ?? [];
      if (!Array.isArray(uwData)) uwData = [];
    }

    // 3. Count months with actual data (years > 0)
    const populatedCount = uwData.filter((m: MonthData) => m.years > 0).length;

    if (populatedCount === 12) {
      // All 12 present — cache as UW source and return
      const entry: CacheEntry = {
        ticker: symbol,
        expires: cacheExpiry(),
        source: "uw",
        data: uwData,
      };
      await writeCache(entry);
      const response = NextResponse.json({ data: uwData, source: "uw", requestId });
      return setCacheResponseHeaders(response, {
        maxAgeSeconds: SEASONALITY_CACHE_SECONDS,
        staleWhileRevalidateSeconds: STALE_REVALIDATE_SECONDS,
        requestId,
        cacheState: "MISS",
        tags: [`ticker-seasonality.${symbol}`],
      });
    }

    // 4. Missing months — try EquityClock Vision fallback
    const visionData = await extractViaVision(symbol, requestId);

    if (visionData) {
      // Build a map of UW data by month
      const uwByMonth = new Map<number, MonthData>();
      for (const m of uwData) {
        if (m.years > 0) uwByMonth.set(m.month, m);
      }

      // Merge: UW takes priority, Vision fills gaps
      const merged: MonthData[] = [];
      for (let month = 1; month <= 12; month++) {
        merged.push(uwByMonth.get(month) ?? visionData.find((v) => v.month === month) ?? {
          month,
          avg_change: 0,
          median_change: 0,
          max_change: 0,
          min_change: 0,
          positive_months_perc: 0,
          years: 0,
        });
      }

      const source = populatedCount > 0 ? "uw+equityclock" : "equityclock";
      // Only persist when the merge actually carries data — a 12-month array
      // of hollow (years: 0) entries must not be cached behind the 24h TTL
      // (feedback_dont_cache_empty_results).
      const mergedPopulated = merged.filter((m: MonthData) => m.years > 0).length;
      if (mergedPopulated > 0) {
        await writeCache({ ticker: symbol, expires: cacheExpiry(), source, data: merged });
      }
      const response = NextResponse.json({ data: merged, source, requestId });
      return setCacheResponseHeaders(response, {
        maxAgeSeconds: mergedPopulated > 0 ? SEASONALITY_CACHE_SECONDS : 30,
        staleWhileRevalidateSeconds: mergedPopulated > 0 ? STALE_REVALIDATE_SECONDS : 120,
        requestId,
        cacheState: "MISS",
        tags: [`ticker-seasonality.${symbol}`],
      });
    }

    // 5. Vision also failed — return UW data as-is (may be partial). Only
    // persist when at least one month actually has data; caching all-hollow
    // months behind the 24h TTL strands the chart empty for a full day.
    if (uwData.length > 0) {
      if (populatedCount > 0) {
        await writeCache({ ticker: symbol, expires: cacheExpiry(), source: "uw", data: uwData });
      }
      const response = NextResponse.json({ data: uwData, source: "uw", requestId });
      return setCacheResponseHeaders(response, {
        maxAgeSeconds: populatedCount > 0 ? SEASONALITY_CACHE_SECONDS : 30,
        staleWhileRevalidateSeconds: populatedCount > 0 ? STALE_REVALIDATE_SECONDS : 120,
        requestId,
        cacheState: "MISS",
        tags: [`ticker-seasonality.${symbol}`],
      });
    }

    const response = NextResponse.json({ data: [], source: "uw", requestId });
    return setCacheResponseHeaders(response, {
      maxAgeSeconds: 30,
      staleWhileRevalidateSeconds: 120,
      requestId,
      cacheState: "MISS",
      tags: [`ticker-seasonality.${symbol}`],
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to fetch seasonality";
    return jsonApiError({
      message,
      status: 500,
      code: "UPSTREAM_ERROR",
      detail: "ticker/seasonality failed",
      requestId,
    });
  }
}
