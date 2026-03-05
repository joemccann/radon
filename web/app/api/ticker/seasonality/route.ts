import { NextResponse } from "next/server";
import { promises as fs } from "fs";
import path from "path";

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

async function extractViaVision(ticker: string): Promise<MonthData[] | null> {
  const apiKey = resolveApiKey();
  if (!apiKey) return null;

  const imageUrl = `https://charts.equityclock.com/seasonal_charts/${encodeURIComponent(ticker)}_sheet.png`;

  // Verify the image exists before sending to Vision
  try {
    const headRes = await fetch(imageUrl, { method: "HEAD" });
    if (!headRes.ok) return null;
  } catch {
    return null;
  }

  try {
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
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
    return null;
  }
}

export async function GET(request: Request): Promise<Response> {
  const { searchParams } = new URL(request.url);
  const ticker = searchParams.get("ticker");

  if (!ticker) {
    return NextResponse.json({ error: "ticker parameter required" }, { status: 400 });
  }

  const symbol = ticker.toUpperCase();

  // 1. Check cache
  const cached = await readCache(symbol);
  if (cached) {
    return NextResponse.json({ data: cached.data, source: cached.source });
  }

  const token = process.env.UW_TOKEN;
  if (!token) {
    return NextResponse.json({ error: "UW_TOKEN not configured" }, { status: 500 });
  }

  try {
    // 2. Fetch UW API
    const res = await fetch(
      `https://api.unusualwhales.com/api/seasonality/${encodeURIComponent(symbol)}/monthly`,
      { headers: { Authorization: `Bearer ${token}` } },
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
      return NextResponse.json({ data: uwData, source: "uw" });
    }

    // 4. Missing months — try EquityClock Vision fallback
    const visionData = await extractViaVision(symbol);

    if (visionData) {
      // Build a map of UW data by month
      const uwByMonth = new Map<number, MonthData>();
      for (const m of uwData) {
        if (m.years > 0) uwByMonth.set(m.month, m);
      }

      // Merge: UW takes priority, Vision fills gaps
      const merged: MonthData[] = [];
      for (let month = 1; month <= 12; month++) {
        merged.push(uwByMonth.get(month) ?? visionData.find(v => v.month === month) ?? {
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
      const entry: CacheEntry = {
        ticker: symbol,
        expires: cacheExpiry(),
        source,
        data: merged,
      };
      await writeCache(entry);
      return NextResponse.json({ data: merged, source });
    }

    // 5. Vision also failed — return UW data as-is (may be partial)
    if (uwData.length > 0) {
      const entry: CacheEntry = {
        ticker: symbol,
        expires: cacheExpiry(),
        source: "uw",
        data: uwData,
      };
      await writeCache(entry);
      return NextResponse.json({ data: uwData, source: "uw" });
    }

    return NextResponse.json({ data: [], source: "uw" });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Failed to fetch seasonality";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
