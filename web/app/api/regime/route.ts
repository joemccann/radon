import { NextResponse } from "next/server";
import { readFile, readdir, writeFile, stat, mkdir } from "fs/promises";
import { join } from "path";
import { isCriDataStale } from "@/lib/criStaleness";
import { selectPreferredCriCandidate, type CriCacheCandidate } from "@/lib/criCache";
import { backfillRealizedVolHistory, type RegimeHistoryEntry } from "@/lib/regimeHistory";
import { radonFetch } from "@/lib/radonApi";

export const runtime = "nodejs";

const DATA_DIR = join(process.cwd(), "..", "data");
const CACHE_PATH = join(DATA_DIR, "cri.json");
const SCHEDULED_DIR = join(DATA_DIR, "cri_scheduled");

/** Today's date in ET (YYYY-MM-DD) — the trading calendar reference */
function todayET(): string {
  return new Date().toLocaleDateString("sv", { timeZone: "America/New_York" });
}

/** Real-time market open check: Mon-Fri, 9:30-16:00 ET */
function isMarketOpenNow(): boolean {
  const now = new Date();
  const etStr = now.toLocaleString("en-US", { timeZone: "America/New_York" });
  const et = new Date(etStr);
  const day = et.getDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false;
  const minutes = et.getHours() * 60 + et.getMinutes();
  return minutes >= 9 * 60 + 30 && minutes <= 16 * 60;
}

const EMPTY_CRI = {
  scan_time: "",
  date: "",
  vix: 0,
  vvix: 0,
  spy: 0,
  vix_5d_roc: 0,
  vvix_vix_ratio: null,
  spx_100d_ma: null,
  spx_distance_pct: 0,
  cor1m: null,
  cor1m_previous_close: null,
  cor1m_5d_change: null,
  realized_vol: null,
  cri: { score: 0, level: "LOW", components: { vix: 0, vvix: 0, correlation: 0, momentum: 0 } },
  cta: { realized_vol: 0, exposure_pct: 200, forced_reduction_pct: 0, est_selling_bn: 0 },
  menthorq_cta: null,
  crash_trigger: { triggered: false, conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false }, values: {} },
  history: [],
  spy_closes: [],
};

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function normalizeCriPayload(raw: Record<string, unknown>): Record<string, unknown> {
  const crashTrigger = (raw.crash_trigger ?? {}) as Record<string, unknown>;
  const conditions = (crashTrigger.conditions ?? {}) as Record<string, unknown>;
  const spyCloses = Array.isArray(raw.spy_closes)
    ? raw.spy_closes.map((value) => asNumber(value)).filter((value): value is number => value !== null)
    : [];
  const history = Array.isArray(raw.history)
    ? backfillRealizedVolHistory(raw.history as RegimeHistoryEntry[], spyCloses)
    : [];
  const latestRealizedVol = history.length > 0 ? asNumber(history[history.length - 1].realized_vol) : null;
  const normalizedRealizedVol = asNumber(raw.realized_vol) ?? latestRealizedVol;

  return {
    ...EMPTY_CRI,
    ...raw,
    cor1m: asNumber(raw.cor1m),
    cor1m_previous_close:
      asNumber(raw.cor1m_previous_close)
      ?? (Array.isArray(raw.history) && raw.history.length > 0
        ? asNumber((raw.history[raw.history.length - 1] as RegimeHistoryEntry).cor1m ?? null)
        : null),
    cor1m_5d_change: asNumber(raw.cor1m_5d_change),
    realized_vol: normalizedRealizedVol,
    history,
    spy_closes: spyCloses,
    crash_trigger: {
      ...EMPTY_CRI.crash_trigger,
      ...crashTrigger,
      conditions: {
        ...EMPTY_CRI.crash_trigger.conditions,
        ...conditions,
        cor1m_gt_60: typeof conditions.cor1m_gt_60 === "boolean" ? conditions.cor1m_gt_60 : false,
      },
    },
  };
}

let bgScanInFlight = false;

/** Read the latest CRI JSON — scheduled dir first, then legacy cri.json.
 *  Iterates newest→oldest, skipping corrupt files (e.g. stderr mixed in). */
async function readLatestCri(): Promise<{ data: object; path: string } | null> {
  async function readCriCandidate(filePath: string): Promise<CriCacheCandidate | null> {
    try {
      const raw = await readFile(filePath, "utf-8");
      const jsonStart = raw.indexOf("{");
      if (jsonStart === -1) return null;
      const fileStat = await stat(filePath);
      return {
        path: filePath,
        mtimeMs: fileStat.mtimeMs,
        data: JSON.parse(raw.slice(jsonStart)) as Record<string, unknown>,
      };
    } catch {
      return null;
    }
  }

  async function readLatestScheduledCri(): Promise<CriCacheCandidate | null> {
    try {
      const files = await readdir(SCHEDULED_DIR);
      const jsonFiles = files.filter((f) => f.startsWith("cri-") && f.endsWith(".json")).sort();
      for (let index = jsonFiles.length - 1; index >= 0; index -= 1) {
        const candidate = await readCriCandidate(join(SCHEDULED_DIR, jsonFiles[index]));
        if (candidate) return candidate;
      }
    } catch {
      // dir may not exist yet
    }

    return null;
  }

  const selected = selectPreferredCriCandidate(
    await readLatestScheduledCri(),
    await readCriCandidate(CACHE_PATH),
  );

  return selected ? { data: selected.data, path: selected.path } : null;
}

/** Check if the latest cached data is stale (market-hours aware). */
async function isCacheStale(filePath: string, data: Record<string, unknown>): Promise<boolean> {
  try {
    const s = await stat(filePath);
    return isCriDataStale(data, s.mtimeMs, todayET());
  } catch {
    return true;
  }
}

/** Fire-and-forget: run CRI scan via FastAPI and save results */
function triggerBackgroundScan(): void {
  if (bgScanInFlight) return;
  bgScanInFlight = true;

  console.log("[CRI] Background scan triggered via FastAPI");
  radonFetch<Record<string, unknown>>("/regime/scan", { method: "POST", timeout: 130_000 })
    .then(async (data) => {
      await mkdir(SCHEDULED_DIR, { recursive: true });
      const ts = new Date().toLocaleString("sv", { timeZone: "America/New_York" })
        .replace(" ", "T").slice(0, 16).replace(":", "-");
      const outPath = join(SCHEDULED_DIR, `cri-${ts}.json`);
      const payload = JSON.stringify(data, null, 2);
      await writeFile(outPath, payload);
      console.log(`[CRI] Background scan complete → ${outPath}`);
    })
    .catch((err) => { console.error("[CRI] Background scan failed:", err.message); })
    .finally(() => { bgScanInFlight = false; });
}

export async function GET(): Promise<Response> {
  const result = await readLatestCri();
  const data = normalizeCriPayload((result?.data ?? EMPTY_CRI) as Record<string, unknown>);

  // Stale-while-revalidate: return cached data immediately,
  // kick off a background scan if data date != today (ET) or file mtime > TTL
  if (!result || await isCacheStale(result.path, data)) {
    triggerBackgroundScan();
  }

  // Override market_open with real-time check when serving stale data from a
  // previous day — prevents "MARKET CLOSED" banner when the market is actually open.
  const today = todayET();
  if ((data as Record<string, unknown>).date !== today) {
    (data as Record<string, unknown>).market_open = isMarketOpenNow();
  }

  return NextResponse.json(data);
}

export async function POST(): Promise<Response> {
  try {
    const rawData = await radonFetch<Record<string, unknown>>("/regime/scan", {
      method: "POST",
      timeout: 130_000,
    });
    const data = normalizeCriPayload(rawData);
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "CRI scan failed";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
