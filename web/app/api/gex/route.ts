import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { isGexDataStale } from "@/lib/gexStaleness";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";

export const runtime = "nodejs";

const DATA_DIR = join(process.cwd(), "..", "data");
const CACHE_PATH = join(DATA_DIR, "gex.json");

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
};

function isMarketOpenNow(): boolean {
  const now = new Date();
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay();
  if (day === 0 || day === 6) return false;
  const minutes = et.getHours() * 60 + et.getMinutes();
  return minutes >= 9 * 60 + 30 && minutes <= 16 * 60;
}

function todayET(): string {
  return new Date().toLocaleDateString("sv", { timeZone: "America/New_York" });
}

async function readCachedGex(): Promise<Record<string, unknown> | null> {
  try {
    const raw = await readFile(CACHE_PATH, "utf-8");
    const jsonStart = raw.indexOf("{");
    if (jsonStart === -1) return null;
    return JSON.parse(raw.slice(jsonStart)) as Record<string, unknown>;
  } catch {
    return null;
  }
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
  };
}

let bgScanInFlight = false;

function triggerBackgroundScan(): void {
  if (bgScanInFlight) return;
  bgScanInFlight = true;

  console.log("[GEX] Background scan triggered via FastAPI");
  radonFetch<Record<string, unknown>>("/gex/scan", { method: "POST", timeout: 130_000 })
    .then(() => { console.log("[GEX] Background scan complete"); })
    .catch((err) => { console.error("[GEX] Background scan failed:", err.message); })
    .finally(() => { bgScanInFlight = false; });
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const cached = await readCachedGex();
  const data = normalizeGexPayload(cached ?? {});
  const currentMarketOpen = isMarketOpenNow();

  (data as Record<string, unknown>).market_open = currentMarketOpen;

  const stale = cached
    ? isGexDataStale(cached as { scan_time?: string; market_open?: boolean }, todayET(), currentMarketOpen)
    : true;

  if (stale) {
    triggerBackgroundScan();
  }

  const response = NextResponse.json(data);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 15,
    staleWhileRevalidateSeconds: 120,
    requestId,
    cacheState: "HIT",
    tags: ["gex"],
  });
}
