/**
 * GEX cache staleness logic — market-hours aware.
 *
 * Anchored to scan_time age (same pattern as VCG staleness).
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

export interface GexDataShape {
  scan_time?: string;
  market_open?: boolean;
  [key: string]: unknown;
}

/**
 * @param data - parsed GEX JSON
 * @param todayET - expected SESSION date in ET (YYYY-MM-DD), injectable for testing
 * @param currentMarketOpen - market open state, injectable for testing
 */
export function isGexDataStale(
  data: GexDataShape,
  todayET: string = mostRecentSessionDate(),
  currentMarketOpen: boolean = isMarketOpenAt(),
): boolean {
  if (!data.scan_time) return true;

  const scanDate = parseScanTime(data.scan_time);
  if (!scanDate) return true;

  const sessionDate = scanTimeToEtDate(data.scan_time);
  if (!sessionDate) return true;

  // Behind the most-recent EXPECTED session → stale. `todayET` defaults to the
  // expected SESSION date (weekend/pre-open aware), so finalized Friday data is
  // not re-fetched all weekend (the off-hours scan-storm fix).
  if (sessionDate !== todayET) return true;

  // Same session + market closed → not stale (serve finalized EOD data).
  if (!currentMarketOpen) return false;

  const scanAge = Date.now() - scanDate.getTime();
  return scanAge > CACHE_TTL_MS;
}
