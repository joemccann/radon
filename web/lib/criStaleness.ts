/**
 * CRI cache staleness logic — market-hours aware.
 *
 * Rule:
 *  - data.date !== today                        → always stale (new trading day)
 *  - market_open === false + date === today
 *    + market now closed                         → NOT stale (EOD data is final)
 *  - market_open === false + date === today
 *    + market now open                           → stale after TTL
 *  - market_open === true  + mtime > TTL        → stale (intraday refresh)
 *  - market_open unknown   + date === today      → fall back to TTL check
 *
 * This prevents the API from continuously re-running cri_scan.py after market
 * close. The launchd CRI service (every 30 min, 4:05 AM–8 PM ET) handles
 * scheduled refreshes; the API only needs to refresh during market hours.
 */

import { lastCompletedSessionDate } from "./marketSession";

const CACHE_TTL_MS = 60_000; // 1 minute — intraday refresh interval

export interface CriDataShape {
  date?: string;
  market_open?: boolean;
  [key: string]: unknown;
}

function isMarketOpenNow(): boolean {
  const now = new Date();
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay(); // 0=Sun, 6=Sat
  if (day === 0 || day === 6) return false;
  const minutes = et.getHours() * 60 + et.getMinutes();
  return minutes >= 9 * 60 + 30 && minutes <= 16 * 60;
}

/**
 * @param data      - parsed CRI JSON (must have date and market_open fields)
 * @param mtimeMs   - file modification time in milliseconds (Date.now()-style)
 * @param todayET   - today's date in ET as YYYY-MM-DD (injected for testability)
 * @param currentMarketOpen - explicit market-open state for deterministic testing
 */
export function isCriDataStale(
  data: CriDataShape,
  mtimeMs: number,
  todayET: string,
  currentMarketOpen: boolean = isMarketOpenNow(),
  lastCompletedSession: string = lastCompletedSessionDate()
): boolean {
  if (!data.date) return true;
  // Session-aware date check: comparing against calendar-today alone made
  // Friday's payload permanently "stale" all weekend, and every /api/regime
  // poll fired a background /regime/scan in an infinite loop (2026-07-26).
  // A payload dated the last COMPLETED session is current by date; only a
  // payload OLDER than that session is stale by date.
  if (data.date !== todayET && data.date < lastCompletedSession) return true;

  // When cached payload says closed but market is open, force refresh after TTL.
  if (data.market_open === false) {
    return currentMarketOpen ? Date.now() - mtimeMs > CACHE_TTL_MS : false;
  }

  // Intraday-refresh TTL applies only while the market is CURRENTLY open —
  // a session-close payload stamped market_open:true is final once the
  // market shuts (otherwise the TTL branch re-arms the weekend scan loop).
  return currentMarketOpen && Date.now() - mtimeMs > CACHE_TTL_MS;
}
