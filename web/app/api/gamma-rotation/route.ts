import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { join } from "path";
import { dbExecute } from "@/lib/dbExecute";
import { cachedRead, invalidateCache } from "@/lib/dbCache";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setCacheResponseHeaders } from "@/lib/apiContracts";
import { isGammaRotationStale } from "@/lib/gammaRotationStaleness";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "gamma_rotation_gap.json");

const EMPTY_ASSET = {
  ticker: "",
  spot: null,
  data_date: "",
  strike_data_date: null,
  net_gamma: null,
  net_gex: null,
  call_gex: null,
  put_gex: null,
  net_delta: null,
  gamma_z: null,
  gamma_1d_change: null,
  gamma_3d_change: null,
  state: "NEUTRAL",
  levels: {},
  spot_vs_flip_pct: null,
};

const EMPTY_GAMMA_ROTATION = {
  scan_time: "",
  market_open: false,
  data_date: "",
  source: "Unusual Whales",
  storage: "turso",
  lookback_days: 0,
  z_window: 63,
  signal: {
    state: "NEUTRAL",
    state_label: "Neutral",
    interpretation: "NORMAL",
    tier: null,
    top_watch: false,
    bottom_watch: false,
    top_score: 0,
    bottom_score: 0,
    grg_z: null,
    raw_spread: null,
    spy_gamma_z: null,
    tlt_gamma_z: null,
    spy_3d_gamma_change: null,
    tlt_3d_gamma_change: null,
    summary: "Cross-asset gamma is near neutral.",
  },
  assets: {
    SPY: { ...EMPTY_ASSET, ticker: "SPY" },
    TLT: { ...EMPTY_ASSET, ticker: "TLT" },
  },
  gates: [],
  history: [],
  top_bottom: {
    top: { active: false, copy: "" },
    bottom: { active: false, copy: "" },
  },
};

function isMarketOpenNow(): boolean {
  const now = new Date();
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay();
  if (day === 0 || day === 6) return false;
  const minutes = et.getHours() * 60 + et.getMinutes();
  return minutes >= 9 * 60 + 30 && minutes <= 16 * 60;
}

// Staleness now lives in the shared, market-gated lib/gammaRotationStaleness.ts
// (closed-guard precedes the date-roll check) so off-hours GETs stop firing a
// background scan on every weekend/overnight tab open.

async function readCachedGammaRotationFromDb(): Promise<Record<string, unknown> | null> {
  try {
    const result = await dbExecute({
      sql: `SELECT payload FROM gamma_rotation_snapshots ORDER BY scan_time DESC LIMIT 1`,
      args: [],
    }, { label: "gamma-rotation" });
    if (result.rows.length === 0) return null;
    const row = result.rows[0] as unknown as { payload: string };
    return JSON.parse(row.payload) as Record<string, unknown>;
  } catch {
    return null;
  }
}

async function readCachedGammaRotationFromDisk(): Promise<Record<string, unknown> | null> {
  try {
    const raw = await readFile(CACHE_PATH, "utf-8");
    const jsonStart = raw.indexOf("{");
    if (jsonStart === -1) return null;
    return JSON.parse(raw.slice(jsonStart)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

// Coalesces every client tab's ~60s poll into one source read per window
// (contract: tests/db-read-cache-contract.test.ts).
const READ_CACHE_TTL_MS = 5_000;

async function readCachedGammaRotation(): Promise<Record<string, unknown> | null> {
  return cachedRead("gamma-rotation:snapshot", READ_CACHE_TTL_MS, async () =>
    (await readCachedGammaRotationFromDb()) ?? (await readCachedGammaRotationFromDisk()),
  );
}

function normalizeAsset(raw: unknown, ticker: "SPY" | "TLT"): Record<string, unknown> {
  return {
    ...EMPTY_ASSET,
    ticker,
    ...(typeof raw === "object" && raw !== null ? raw as Record<string, unknown> : {}),
  };
}

function normalizeGammaRotationPayload(raw: Record<string, unknown>): Record<string, unknown> {
  const signal = typeof raw.signal === "object" && raw.signal !== null ? raw.signal as Record<string, unknown> : {};
  const assets = typeof raw.assets === "object" && raw.assets !== null ? raw.assets as Record<string, unknown> : {};
  const topBottom = typeof raw.top_bottom === "object" && raw.top_bottom !== null ? raw.top_bottom as Record<string, unknown> : {};

  return {
    ...EMPTY_GAMMA_ROTATION,
    ...raw,
    scan_time: typeof raw.scan_time === "string" ? raw.scan_time : "",
    market_open: typeof raw.market_open === "boolean" ? raw.market_open : isMarketOpenNow(),
    signal: { ...EMPTY_GAMMA_ROTATION.signal, ...signal },
    assets: {
      SPY: normalizeAsset(assets.SPY, "SPY"),
      TLT: normalizeAsset(assets.TLT, "TLT"),
    },
    gates: Array.isArray(raw.gates) ? raw.gates : [],
    history: Array.isArray(raw.history) ? raw.history : [],
    top_bottom: {
      ...EMPTY_GAMMA_ROTATION.top_bottom,
      ...topBottom,
    },
  };
}

let bgScanInFlight = false;

function triggerBackgroundScan(): void {
  if (bgScanInFlight) return;
  bgScanInFlight = true;
  console.log("[GRG] Background scan triggered via FastAPI");
  radonFetch<Record<string, unknown>>("/gamma-rotation/scan", { method: "POST", timeout: 130_000 })
    .then(() => { console.log("[GRG] Background scan complete"); })
    .catch((err) => { console.error("[GRG] Background scan failed:", err.message); })
    .finally(() => { bgScanInFlight = false; });
}

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const cached = await readCachedGammaRotation();
  const data = normalizeGammaRotationPayload(cached ?? {});
  const currentMarketOpen = isMarketOpenNow();

  data.market_open = currentMarketOpen;

  if (!cached || isGammaRotationStale(cached, undefined, currentMarketOpen)) {
    triggerBackgroundScan();
  }

  const response = NextResponse.json(data);
  return setCacheResponseHeaders(response, {
    maxAgeSeconds: 15,
    staleWhileRevalidateSeconds: 120,
    requestId,
    cacheState: "HIT",
    tags: ["gamma-rotation"],
  });
}

export async function POST(): Promise<Response> {
  try {
    const rawData = await radonFetch<Record<string, unknown>>("/gamma-rotation/scan", { method: "POST", timeout: 130_000 });
    invalidateCache("gamma-rotation:snapshot");
    return NextResponse.json(normalizeGammaRotationPayload(rawData));
  } catch (error) {
    const message = error instanceof Error ? error.message : "Gamma Rotation Gap scan failed";
    const cached = await readCachedGammaRotation();
    if (cached) {
      const res = NextResponse.json(normalizeGammaRotationPayload(cached));
      res.headers.set("X-Sync-Warning", `Gamma Rotation Gap scan failed - serving cached data (${message})`);
      return res;
    }
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
