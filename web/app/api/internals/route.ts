import { requireRouteAccess } from "@/lib/routeAccess";


export const runtime = "nodejs";

import { NextResponse } from "next/server";
import { writeFile, mkdir } from "fs/promises";
import { join } from "path";
import { isCriDataStale } from "@/lib/criStaleness";
import { hasCompleteRvolHistory } from "@/lib/criCache";
import { backfillRealizedVolHistory, type RegimeHistoryEntry } from "@/lib/regimeHistory";
import { radonFetch } from "@/lib/radonApi";
import { isSkewCacheFresh } from "@/lib/internalsSkewCache";
import { dbExecute } from "@/lib/dbExecute";
import { contentTimestampMs, dbFirstRead, type TimestampedRead } from "@/lib/dbFirstRead";
import { getRequestId, setNoStoreResponseHeaders, scrubSecrets } from "@/lib/apiContracts";
import {
  toLongRangeSkewPoints,
  type LongRangeSkewHistoryPayload,
} from "@/lib/internalsSkewSeries";

// Disable Next.js static caching: this handler reads live Turso state. Without
// this, the framework freezes the first response and serves stale data until
// the dev server restarts.
export const dynamic = "force-dynamic";

const PROJECT_ROOT = join(/* turbopackIgnore: true */ process.cwd(), "..");
// Write-only archive consumed by scripts/strength_confirmation_scanner.py and
// scripts/generate_regime_share.py. This route never reads it back.
const SCHEDULED_DIR = join(PROJECT_ROOT, "data", "cri_scheduled");
// Bound on the Turso reads below; memory serves past it.
const DB_READ_TIMEOUT_MS = 2500;
// Enough sessions to chart; menthorq_cta holds one row per trading day.
const MENTHORQ_SKEW_HISTORY_ROWS = 400;

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
  vix: null,
  vvix: null,
  spy: null,
  vix_5d_roc: null,
  vvix_vix_ratio: null,
  spx_100d_ma: null,
  spx_distance_pct: null,
  cor1m: null,
  cor1m_previous_close: null,
  cor1m_5d_change: null,
  realized_vol: null,
  cri: { score: 0, level: "LOW", components: { vix: 0, vvix: 0, correlation: 0, momentum: 0 } },
  cta: { realized_vol: 0, exposure_pct: 200, forced_reduction_pct: 0, est_selling_bn: 0 },
  menthorq_cta: null,
  spx_position: null,
  spx_skew: null,
  crash_trigger: { triggered: false, conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false }, values: {} },
  history: [],
  spy_closes: [],
  nasdaq_skew: null,
  nq_skew: null,
  nq_skew_history: [] as Array<{ date: string; nq_skew: number; spx_position: number; nq_position: number }>,
};

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function normalizeCriComponents(raw: Record<string, unknown> | undefined) {
  const components = raw ?? {};
  return {
    vix: asNumber(components.vix) ?? EMPTY_CRI.cri.components.vix,
    vvix: asNumber(components.vvix) ?? EMPTY_CRI.cri.components.vvix,
    correlation: asNumber(components.correlation) ?? EMPTY_CRI.cri.components.correlation,
    momentum: asNumber(components.momentum) ?? EMPTY_CRI.cri.components.momentum,
  };
}

function asMenthorqTables(raw: unknown): Record<string, Array<Record<string, unknown>>> {
  if (!raw || typeof raw !== "object") return {};
  const tables = raw as Record<string, unknown>;
  const safe: Record<string, Array<Record<string, unknown>>> = {};
  for (const key of Object.keys(tables)) {
    const rows = tables[key];
    safe[key] = Array.isArray(rows) ? rows.filter((r): r is Record<string, unknown> => typeof r === "object" && r !== null) : [];
  }
  return safe;
}

function hasAllKeywords(value: unknown, keywords: string[]): boolean {
  if (!value || typeof value !== "object") return false;
  const raw = value as Record<string, unknown>;
  const underlying = typeof raw.underlying === "string" ? raw.underlying.toLowerCase() : "";
  return keywords.every((keyword) => underlying.includes(keyword.toLowerCase()));
}

function collectMenthorqRows(menthorqCache: Record<string, unknown> | null): Array<Record<string, unknown>> {
  if (!menthorqCache) return [];
  const tables = asMenthorqTables(menthorqCache.tables);
  const rows: Array<Record<string, unknown>> = [];
  for (const rowsForTable of Object.values(tables)) {
    rows.push(...rowsForTable);
  }
  return rows;
}

function findMenthorqRow(rows: Array<Record<string, unknown>>, ...keywords: string[]): Record<string, unknown> | null {
  for (const row of rows) {
    if (hasAllKeywords(row, keywords)) return row;
  }
  return null;
}

function coerceMenthorqForSkew(raw: Record<string, unknown>): Record<string, unknown> | null {
  const menthorq = raw.menthorq_cta;
  if (!menthorq || typeof menthorq !== "object") return null;
  const payload = menthorq as Record<string, unknown>;
  if (!payload.tables || typeof payload.tables !== "object") return null;
  return { tables: payload.tables };
}

function readMenthorqRowsForDate(raw: Record<string, unknown> | null): Array<Record<string, unknown>> {
  if (!raw) return [];
  if (raw.menthorq_cta && typeof raw.menthorq_cta === "object") {
    return collectMenthorqRows(coerceMenthorqForSkew(raw) ?? raw);
  }
  return collectMenthorqRows(raw);
}

function extractNqSkewFromMenthorqRows(rows: Array<Record<string, unknown>>): number | null {
  const spx = findMenthorqRowAny(rows, [["s&p"], ["spx"]]);
  const nq = findMenthorqRow(rows, "nasdaq");
  const spxPos = asNumber(spx?.position_today) ?? asNumber(spx?.position);
  const nqPos = asNumber(nq?.position_today) ?? asNumber(nq?.position);
  if (spxPos == null || nqPos == null) return null;
  return nqPos - spxPos;
}

function extractNqSkewFromMenthorqPayload(raw: Record<string, unknown> | null): {
  nq_skew: number | null;
  spx_position: number | null;
  nq_position: number | null;
} {
  const rows = readMenthorqRowsForDate(raw);
  if (rows.length === 0) {
    return { nq_skew: null, spx_position: null, nq_position: null };
  }

  const spx = findMenthorqRowAny(rows, [["s&p"], ["spx"]]);
  const nq = findMenthorqRow(rows, "nasdaq");
  const spxPos = asNumber(spx?.position_today) ?? asNumber(spx?.position);
  const nqPos = asNumber(nq?.position_today) ?? asNumber(nq?.position);
  if (spxPos == null && nqPos == null) {
    return { nq_skew: null, spx_position: null, nq_position: null };
  }

  return {
    nq_skew: spxPos == null || nqPos == null ? null : nqPos - spxPos,
    spx_position: spxPos,
    nq_position: nqPos,
  };
}

type MenthorqSkewHistoryPoint = {
  date: string;
  metric: "cta_position_spread" | "option_rr_spread";
  nq_skew: number | null;
  spx_position: number | null;
  nq_position: number | null;
  spx_skew?: number | null;
};

/** Menthorq CTA position-spread history from Turso (last-in-memory on failure). */
async function readMenthorqSkewHistory(): Promise<MenthorqSkewHistoryPoint[]> {
  const result = await dbFirstRead({
    fromDb: readMenthorqSkewHistoryFromDb,
    // Unused: the route only charts this history.
    maxAgeMs: DB_READ_TIMEOUT_MS,
    label: "internals-menthorq-history",
    sourceTimeoutMs: DB_READ_TIMEOUT_MS,
  });
  return result.ok ? result.data : [];
}

async function readMenthorqSkewHistoryFromDb(): Promise<TimestampedRead<MenthorqSkewHistoryPoint[]> | null> {
  const result = await dbExecute({
    sql: `SELECT date, payload FROM menthorq_cta ORDER BY date DESC LIMIT ?`,
    args: [MENTHORQ_SKEW_HISTORY_ROWS],
  }, { label: "internals-menthorq-history", timeoutMs: DB_READ_TIMEOUT_MS });
  if (result.rows.length === 0) return null;

  const rows = (result.rows as unknown as Array<{ date: string; payload: string }>).slice().reverse();
  const points: MenthorqSkewHistoryPoint[] = [];
  for (const row of rows) {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(row.payload) as Record<string, unknown>;
    } catch {
      continue;
    }
    const extracted = extractNqSkewFromMenthorqPayload(payload);
    if (extracted.nq_skew == null && extracted.spx_position == null && extracted.nq_position == null) continue;
    points.push({
      date: asString(payload.date) || row.date,
      metric: "cta_position_spread",
      nq_skew: extracted.nq_skew,
      spx_position: extracted.spx_position,
      nq_position: extracted.nq_position,
      spx_skew: extracted.spx_position,
    });
  }
  return { data: points, timestampMs: contentTimestampMs(rows[rows.length - 1].date) };
}

// FastAPI's /internals/skew-history is the source; its own UW cache sits behind
// it. The last good response is held here so a poll within a calendar day stays
// off the network and an outage keeps serving it.
let lastLongRangeSkewHistory: MenthorqSkewHistoryPoint[] = [];

async function readInternalsSkewHistory(): Promise<MenthorqSkewHistoryPoint[]> {
  const longRangeHistory = await readLongRangeSkewHistory();
  if (longRangeHistory.length > 0) return longRangeHistory;
  return readMenthorqSkewHistory();
}

function toSafeNumber(value: unknown): number | null {
  return asNumber(value);
}

async function readLongRangeSkewHistory(): Promise<MenthorqSkewHistoryPoint[]> {
  const cached = lastLongRangeSkewHistory;

  // If memory already has recent data, serve it without a network round-trip.
  // "Recent" = latest data-point within 1 calendar day of today (ET).
  if (cached.length > 0) {
    const latestDate = cached[cached.length - 1].date;
    if (isSkewCacheFresh(latestDate, todayET())) {
      return cached;
    }
  }

  // Memory is stale or empty — fetch fresh from FastAPI (which has its own
  // 15-min UW cache).  This runs even when the market is closed so that
  // overnight / weekend UW publishes are picked up.
  try {
    const response = await radonFetch<LongRangeSkewHistoryPayload>("/internals/skew-history", {
      method: "GET",
      timeout: 90_000,
    });
    const points = toLongRangeSkewPoints(response);
    if (points.length > 0) {
      lastLongRangeSkewHistory = points;
      return points;
    }
  } catch {
    // fall through to the last good response
  }

  return cached;
}

function findMenthorqRowAny(rows: Array<Record<string, unknown>>, groups: string[][]): Record<string, unknown> | null {
  for (const row of rows) {
    const candidate = row as Record<string, unknown>;
    const underlying = typeof candidate.underlying === "string" ? candidate.underlying.toLowerCase() : "";
    const matched = groups.some((keywords) => keywords.every((keyword) => underlying.includes(keyword.toLowerCase())));
    if (matched) return row;
  }
  return null;
}

function computeNasdaqSkew(raw: Record<string, unknown>, menthorqCache: Record<string, unknown> | null): number | null {
  const source = menthorqCache ?? raw;
  return extractNqSkewFromMenthorqPayload(source).nq_skew;
}

function extractCurrentMenthorqSkew(raw: Record<string, unknown>, menthorqCache: Record<string, unknown> | null): {
  nq_skew: number | null;
  spx_position: number | null;
} {
  const rawPayload = menthorqCache ?? raw;
  const skew = extractNqSkewFromMenthorqPayload(rawPayload);
  return {
    nq_skew: skew.nq_skew,
    spx_position: skew.spx_position,
  };
}

function normalizeCriPayload(
  raw: Record<string, unknown>,
  menthorqCache: Record<string, unknown> | null,
  menthorqSkewHistory: MenthorqSkewHistoryPoint[] = [],
) {
  const nqSkewHistory = menthorqSkewHistory.filter((entry) => entry.metric === "option_rr_spread");
  const ctaPositionHistory = menthorqSkewHistory.filter((entry) => entry.metric === "cta_position_spread");
  const spxSkewHistory = nqSkewHistory
    .map((entry) => ({
      date: entry.date,
      spx_skew: entry.spx_skew,
    }))
    .filter((entry): entry is { date: string; spx_skew: number } => entry.spx_skew != null);

  const crashTrigger = (raw.crash_trigger as Record<string, unknown>) ?? {};
  const conditions = (crashTrigger.conditions as Record<string, unknown>) ?? {};
  const spyCloses = Array.isArray(raw.spy_closes)
    ? raw.spy_closes
      .map((value) => asNumber(value))
      .filter((value): value is number => value !== null)
    : [];
  const history = Array.isArray(raw.history)
    ? backfillRealizedVolHistory(raw.history as RegimeHistoryEntry[], spyCloses)
    : [];
  const latestHistoryCor1m = history.length > 0
    ? asNumber(history[history.length - 1].cor1m)
    : null;
  const latestRealizedVol = history.length > 0 ? asNumber(history[history.length - 1].realized_vol) : null;
  const normalizedRealizedVol = asNumber(raw.realized_vol) ?? latestRealizedVol;

  const rawCri = (raw.cri as Record<string, unknown>) ?? {};
  const rawCriLevel = asString(rawCri.level);
  const normalizedCriLevel = ["LOW", "ELEVATED", "HIGH", "CRITICAL"].includes(rawCriLevel)
    ? rawCriLevel
    : EMPTY_CRI.cri.level;
  const rawCta = (raw.cta as Record<string, unknown>) ?? {};
  const currentMenthorqSkew = extractCurrentMenthorqSkew(raw, menthorqCache);
  const latestSkewPoint = nqSkewHistory.length > 0
    ? nqSkewHistory[nqSkewHistory.length - 1]
    : null;
  const latestNqSkew = latestSkewPoint?.metric === "option_rr_spread" ? latestSkewPoint.nq_skew : null;
  const latestSpxSkew = latestSkewPoint?.metric === "option_rr_spread" ? latestSkewPoint.spx_skew : null;

  return {
    ...EMPTY_CRI,
    scan_time: asString(raw.scan_time),
    date: asString(raw.date),
    market_open: asBoolean(raw.market_open),
    vix: asNumber(raw.vix),
    vvix: asNumber(raw.vvix),
    spy: asNumber(raw.spy),
    vix_5d_roc: asNumber(raw.vix_5d_roc),
    vvix_vix_ratio: asNumber(raw.vvix_vix_ratio),
    spx_100d_ma: asNumber(raw.spx_100d_ma),
    spx_distance_pct: asNumber(raw.spx_distance_pct),
    cor1m: asNumber(raw.cor1m),
    cor1m_previous_close:
      asNumber(raw.cor1m_previous_close) ?? latestHistoryCor1m ?? EMPTY_CRI.cor1m_previous_close,
    cor1m_5d_change: asNumber(raw.cor1m_5d_change),
    realized_vol: normalizedRealizedVol,
    cri: {
      ...EMPTY_CRI.cri,
      ...rawCri,
      score: asNumber(rawCri.score) ?? EMPTY_CRI.cri.score,
      level: normalizedCriLevel,
      components: {
        ...EMPTY_CRI.cri.components,
        ...normalizeCriComponents(rawCri.components as Record<string, unknown>),
      },
    },
    cta: {
      ...EMPTY_CRI.cta,
      ...rawCta,
      realized_vol: asNumber(rawCta.realized_vol) ?? EMPTY_CRI.cta.realized_vol,
      exposure_pct: asNumber(rawCta.exposure_pct) ?? EMPTY_CRI.cta.exposure_pct,
      forced_reduction_pct:
        asNumber(rawCta.forced_reduction_pct) ?? EMPTY_CRI.cta.forced_reduction_pct,
      est_selling_bn: asNumber(rawCta.est_selling_bn) ?? EMPTY_CRI.cta.est_selling_bn,
    },
    menthorq_cta: raw.menthorq_cta ?? null,
    history,
    spy_closes: spyCloses,
    nasdaq_skew: latestNqSkew,
    nq_skew: latestNqSkew,
    spx_position: latestSpxSkew,
    spx_skew: latestSpxSkew,
    nq_skew_history: nqSkewHistory,
    spx_skew_history: spxSkewHistory,
    cta_position_history: ctaPositionHistory,
    cta_position_spread: currentMenthorqSkew.nq_skew,
    crash_trigger: {
      ...EMPTY_CRI.crash_trigger,
      ...crashTrigger,
      triggered:
        typeof crashTrigger.triggered === "boolean" ? crashTrigger.triggered : EMPTY_CRI.crash_trigger.triggered,
      conditions: {
        ...EMPTY_CRI.crash_trigger.conditions,
        ...conditions,
        spx_below_100d_ma:
          typeof conditions.spx_below_100d_ma === "boolean"
            ? conditions.spx_below_100d_ma
            : EMPTY_CRI.crash_trigger.conditions.spx_below_100d_ma,
        realized_vol_gt_25:
          typeof conditions.realized_vol_gt_25 === "boolean"
            ? conditions.realized_vol_gt_25
            : EMPTY_CRI.crash_trigger.conditions.realized_vol_gt_25,
        cor1m_gt_60: typeof conditions.cor1m_gt_60 === "boolean" ? conditions.cor1m_gt_60 : false,
      },
      values:
        typeof crashTrigger.values === "object" && crashTrigger.values !== null
          ? crashTrigger.values
          : {},
    },
  };
}

let bgScanInFlight = false;

type LatestCri = { data: Record<string, unknown>; takenAtMs: number | null };

async function readLatestCriFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  // Same bounded read as the regime route: dbExecute caps the read and drops a
  // wedged client, and the last good read serves this request from memory.
  const result = await dbExecute({
    sql: `SELECT taken_at, payload FROM cri_snapshots ORDER BY date DESC, taken_at DESC LIMIT 1`,
    args: [],
  }, { label: "internals-cri", timeoutMs: DB_READ_TIMEOUT_MS });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { taken_at: string; payload: string };
  return {
    data: JSON.parse(row.payload) as Record<string, unknown>,
    timestampMs: contentTimestampMs(row.taken_at),
  };
}

/** Latest CRI snapshot from Turso, or the last good read held in memory. */
async function readLatestCri(): Promise<LatestCri | null> {
  const result = await dbFirstRead({
    fromDb: readLatestCriFromDb,
    // Unused: isCriDataStale judges the served read's own date and taken_at.
    maxAgeMs: DB_READ_TIMEOUT_MS,
    label: "internals-cri",
    sourceTimeoutMs: DB_READ_TIMEOUT_MS,
    // A row with a truncated RVOL history loses to the last complete read.
    isDegraded: (data) => !hasCompleteRvolHistory(data),
  });
  return result.ok ? { data: result.data, takenAtMs: result.timestampMs } : null;
}

async function readLatestMenthorqCacheFromDb(): Promise<TimestampedRead<Record<string, unknown>> | null> {
  const result = await dbExecute({
    sql: `SELECT date, payload FROM menthorq_cta ORDER BY date DESC LIMIT 1`,
    args: [],
  }, { label: "internals-menthorq", timeoutMs: DB_READ_TIMEOUT_MS });
  if (result.rows.length === 0) return null;
  const row = result.rows[0] as unknown as { date: string; payload: string };
  const payload = JSON.parse(String(row.payload)) as Record<string, unknown>;
  return { data: payload, timestampMs: contentTimestampMs(row.date) };
}

/** Latest Menthorq CTA payload from Turso, or the last good one held in memory. */
async function readLatestMenthorqCache(): Promise<Record<string, unknown> | null> {
  const result = await dbFirstRead({
    fromDb: readLatestMenthorqCacheFromDb,
    // Unused: this read only feeds a chart overlay.
    maxAgeMs: DB_READ_TIMEOUT_MS,
    label: "internals-menthorq",
    sourceTimeoutMs: DB_READ_TIMEOUT_MS,
    // A row without tables carries nothing to extract.
    isDegraded: (payload) => !payload.tables || typeof payload.tables !== "object",
  });
  return result.ok && result.data.tables && typeof result.data.tables === "object"
    ? result.data
    : null;
}

/** Check if the latest snapshot is stale (market-hours aware). */
function isCacheStale(takenAtMs: number | null, data: Record<string, unknown>): boolean {
  if (takenAtMs === null) return true;
  return isCriDataStale(data, takenAtMs, todayET(), isMarketOpenNow());
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

export const radonCapability = { GET: "read", POST: "read.spawn" };

export async function GET(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "internals:route", limit: 20, windowMs: 60_000 }, durableRateTier: "A" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  const result = await readLatestCri();
  const [menthorqCache, menthorqSkewHistory] = await Promise.all([
    readLatestMenthorqCache(),
    readInternalsSkewHistory(),
  ]);
  const data = normalizeCriPayload(result?.data ?? EMPTY_CRI, menthorqCache, menthorqSkewHistory);
  const currentMarketOpen = isMarketOpenNow();

  // Keep market_open aligned with the current session state for every request.
  (data as Record<string, unknown>).market_open = currentMarketOpen;

  // Stale-while-revalidate: return cached data immediately,
  // kick off a background scan if today's data is stale or from stale date.
  if (!result || isCacheStale(result.takenAtMs, data)) {
    triggerBackgroundScan();
  }

  return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
}

export async function POST(): Promise<Response> {
  const access = await requireRouteAccess(undefined, { rate: { key: "internals:route", limit: 20, windowMs: 60_000 }, durableRateTier: "B" });
  if (!access.ok) return access.response;
  const requestId = getRequestId();
  try {
    const rawData = await radonFetch<Record<string, unknown>>("/regime/scan", {
      method: "POST",
      timeout: 130_000,
    });
    const [menthorqCache, menthorqSkewHistory] = await Promise.all([
      readLatestMenthorqCache(),
      readInternalsSkewHistory(),
    ]);
    const data = normalizeCriPayload(rawData, menthorqCache, menthorqSkewHistory);
    return setNoStoreResponseHeaders(NextResponse.json(data), requestId);
  } catch (error) {
    const message = scrubSecrets(error instanceof Error ? error.message : "CRI scan failed");
    return setNoStoreResponseHeaders(
      NextResponse.json({ error: message }, { status: 502 }),
      requestId,
    );
  }
}
