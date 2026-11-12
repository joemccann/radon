/**
 * VCG cache staleness logic — market-hours aware.
 *
 * Anchored to scan_time age (not file mtime) since VCG doesn't emit
 * a top-level `date` field like CRI does.
 *
 * Rules:
 *  - scan_time missing/unparseable              → always stale
 *  - session date (from scan_time) !== today ET  → stale (new trading day)
 *  - market open + scan_time age > 60s          → stale (intraday refresh)
 *  - market closed + session date === today      → not stale (EOD data is final)
 */

import { parseScanTime, scanTimeToEtDate } from "./parseScanTime";
import { mostRecentSessionDate } from "./marketSession";
import { isMarketOpenAt } from "@/lib/marketCalendar";

const CACHE_TTL_MS = 60_000; // 1 minute

export interface VcgDataShape {
  scan_time?: string;
  market_open?: boolean;
  [key: string]: unknown;
}

/**
 * @param data - parsed VCG JSON
 * @param todayET - today's date in ET (YYYY-MM-DD), injectable for testing
 * @param currentMarketOpen - market open state, injectable for testing
 */
export function isVcgDataStale(
  data: VcgDataShape,
  todayET: string = mostRecentSessionDate(),
  currentMarketOpen: boolean = isMarketOpenAt(),
): boolean {
  // No scan_time → always stale
  if (!data.scan_time) return true;

  const scanDate = parseScanTime(data.scan_time);
  if (!scanDate) return true;

  const sessionDate = scanTimeToEtDate(data.scan_time);
  if (!sessionDate) return true;

  // Behind the most-recent EXPECTED session → stale (new trading day, or
  // catch-up if a scan was missed). `todayET` defaults to the expected SESSION
  // date (weekend/pre-open aware via mostRecentSessionDate), so on Saturday it
  // is Friday and finalized Friday data is NOT flagged stale all weekend.
  if (sessionDate !== todayET) return true;

  // Same session + market closed → not stale (serve finalized EOD data)
  if (!currentMarketOpen) return false;

  // Market open → stale if scan_time age exceeds TTL
  const scanAge = Date.now() - scanDate.getTime();
  return scanAge > CACHE_TTL_MS;
}
