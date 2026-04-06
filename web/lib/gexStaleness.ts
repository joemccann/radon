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

const CACHE_TTL_MS = 60_000; // 1 minute

export interface GexDataShape {
  scan_time?: string;
  market_open?: boolean;
  [key: string]: unknown;
}

function isMarketOpenNow(): boolean {
  const now = new Date();
  const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const day = et.getDay();
  if (day === 0 || day === 6) return false;
  const minutes = et.getHours() * 60 + et.getMinutes();
  return minutes >= 9 * 60 + 30 && minutes <= 16 * 60;
}

function todayInET(): string {
  return new Date().toLocaleDateString("sv", { timeZone: "America/New_York" });
}

function scanTimeToETDate(scanTime: string): string | null {
  try {
    const d = new Date(scanTime);
    if (isNaN(d.getTime())) return null;
    return d.toLocaleDateString("sv", { timeZone: "America/New_York" });
  } catch {
    return null;
  }
}

/**
 * @param data - parsed GEX JSON
 * @param todayET - today's date in ET (YYYY-MM-DD), injectable for testing
 * @param currentMarketOpen - market open state, injectable for testing
 */
export function isGexDataStale(
  data: GexDataShape,
  todayET: string = todayInET(),
  currentMarketOpen: boolean = isMarketOpenNow(),
): boolean {
  if (!data.scan_time) return true;

  const sessionDate = scanTimeToETDate(data.scan_time);
  if (!sessionDate) return true;

  if (sessionDate !== todayET) return true;

  if (!currentMarketOpen) return false;

  const scanAge = Date.now() - new Date(data.scan_time).getTime();
  return scanAge > CACHE_TTL_MS;
}
