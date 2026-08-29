/**
 * Staleness logic for per-ticker flow reports.
 *
 * A flow report is considered fresh while the market is open if it was
 * generated within the TTL. After hours, an EOD report from the current
 * trading day is considered fresh until the next session.
 *
 * Mirrors the pattern used by `gexStaleness.ts` and `vcgStaleness.ts`.
 */

import { parseScanTime } from "./parseScanTime";

const MARKET_HOURS_TTL_MS = 10 * 60 * 1000; // 10 minutes
const AFTER_HOURS_TTL_MS = 8 * 60 * 60 * 1000; // 8 hours

export type FlowReportLike = {
  fetched_at?: string | null;
  analysis_time?: string | null;
  cache_meta?: {
    last_refresh?: string | null;
    age_seconds?: number | null;
  } | null;
};

export function flowReportTimestamp(report: FlowReportLike | null | undefined): string | null {
  if (!report) return null;
  return (
    report.fetched_at
    ?? report.analysis_time
    ?? report.cache_meta?.last_refresh
    ?? null
  );
}

function isMarketOpenNow(now: Date = new Date()): boolean {
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay();
  if (day === 0 || day === 6) return false;
  const minutes = et.getHours() * 60 + et.getMinutes();
  return minutes >= 9 * 60 + 30 && minutes <= 16 * 60;
}

/**
 * @param report - parsed flow report
 * @param now - injectable clock for testing
 * @param marketOpenOverride - injectable market state for testing
 */
export function isFlowReportStale(
  report: FlowReportLike | null | undefined,
  now: Date = new Date(),
  marketOpenOverride?: boolean,
): boolean {
  const ts = flowReportTimestamp(report);
  if (!ts) return true;

  const parsed = parseScanTime(ts);
  if (!parsed) return true;
  const timestamp = parsed.getTime();

  const ageMs = now.getTime() - timestamp;
  if (ageMs < 0) return false;

  const marketOpen = marketOpenOverride ?? isMarketOpenNow(now);
  const ttl = marketOpen ? MARKET_HOURS_TTL_MS : AFTER_HOURS_TTL_MS;
  return ageMs > ttl;
}

export const FLOW_REPORT_STALENESS = {
  MARKET_HOURS_TTL_MS,
  AFTER_HOURS_TTL_MS,
};

/** Report date in market time, `YYYY-MM-DD` — the format the daily history
 * table already uses. UTC would move an after-hours scan to the next day. */
function marketDate(when: Date): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(when);
}

function calendarDaysApart(from: string, to: string): number {
  return Math.round(
    (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000,
  );
}

/**
 * "2026-06-16 · 73 days old" — the age of a report, for rendering next to the
 * figures it produced. Returns null when no timestamp is usable.
 *
 * A cached flow report is served whenever a live scan fails, and the cache has
 * no upper age: /flow-analysis/AMZN served a June report through August. The
 * verdict, the aggregate and the options bias all render identically to a
 * fresh scan, so the age has to travel with them.
 */
export function flowReportAgeLabel(
  report: FlowReportLike | null | undefined,
  now: Date = new Date(),
): string | null {
  const ts = flowReportTimestamp(report);
  if (!ts) return null;
  const parsed = parseScanTime(ts);
  if (!parsed) return null;

  const reportDate = marketDate(parsed);
  const days = Math.max(0, calendarDaysApart(reportDate, marketDate(now)));
  if (days === 0) return `${reportDate} · today`;
  if (days === 1) return `${reportDate} · 1 day old`;
  return `${reportDate} · ${days} days old`;
}
