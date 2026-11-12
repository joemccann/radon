/**
 * Gamma Rotation Gap (GRG) cache staleness — market-hours aware.
 *
 * Mirrors vcgStaleness / gexStaleness: the market-closed guard runs BEFORE the
 * date-roll check so finalized end-of-day data is never re-fetched off-hours
 * (the ET calendar date rolls forward overnight/weekends, but the prior
 * session's data is final and re-scanning it just burns UW quota).
 *
 * Rules:
 *  - scan_time missing/unparseable        → stale (need an initial scan)
 *  - market closed                        → NOT stale (serve finalized data)
 *  - session date !== today ET (open)     → stale (new trading day)
 *  - market open + payload says closed     → stale (catch up to the live session)
 *  - otherwise                            → not stale
 */

import { scanTimeToEtDate } from "./parseScanTime";
import { mostRecentSessionDate } from "./marketSession";
import { isMarketOpenAt } from "@/lib/marketCalendar";

export interface GammaRotationDataShape {
  scan_time?: string;
  market_open?: boolean;
  [key: string]: unknown;
}

export function isGammaRotationStale(
  data: GammaRotationDataShape,
  todayET: string = mostRecentSessionDate(),
  currentMarketOpen: boolean = isMarketOpenAt(),
): boolean {
  const scanTime = typeof data.scan_time === "string" ? data.scan_time : "";
  const sessionDate = scanTimeToEtDate(scanTime);
  if (!sessionDate) return true;

  // Behind the most-recent EXPECTED session → stale. `todayET` defaults to the
  // expected SESSION date (weekend/pre-open aware), so finalized Friday data is
  // not re-fetched all weekend (the off-hours scan-storm fix).
  if (sessionDate !== todayET) return true;

  // Same session + market closed → not stale (serve finalized data).
  if (!currentMarketOpen) return false;

  // Market open but the cached payload was captured while closed → refresh.
  return data.market_open === false;
}
