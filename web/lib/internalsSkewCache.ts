/**
 * Staleness check for internals skew history cache.
 *
 * The UW risk-reversal skew API publishes end-of-day data after market close.
 * readLongRangeSkewHistory() previously blocked all fresh fetches when the
 * market was closed, causing stale data to persist across sessions.  This
 * helper lets the caller decide whether the disk cache is still fresh enough
 * to serve without hitting the FastAPI backend.
 */

/**
 * Returns `true` when the cached skew data is recent enough to serve
 * directly (no fresh fetch needed).
 *
 * "Fresh" means the latest data-point date is within `maxStaleDays`
 * calendar days of `todayET`.  The default of 1 day covers:
 *   - same-day cache → fresh
 *   - previous-day cache → fresh (handles overnight / pre-market)
 *   - 2+ days behind  → stale  (triggers a fresh fetch)
 */
export function isSkewCacheFresh(
  latestDate: string,
  todayET: string,
  maxStaleDays = 1,
): boolean {
  const latestMs = new Date(`${latestDate}T00:00:00`).getTime();
  const todayMs = new Date(`${todayET}T00:00:00`).getTime();

  if (Number.isNaN(latestMs) || Number.isNaN(todayMs)) return false;

  const diffDays = (todayMs - latestMs) / (1000 * 60 * 60 * 24);
  return diffDays <= maxStaleDays;
}

type SkewVariant = {
  ticker?: unknown;
  delta?: unknown;
  timeframe?: unknown;
  expiries?: unknown;
};

export function matchesInternalsSkewVariant(
  payload: { nq?: SkewVariant; spx?: SkewVariant },
  expected: {
    nqTicker: string;
    spxTicker: string;
    timeframe: string;
    nqDelta: number;
    spxDelta: number;
    nqExpiry?: string | null;
    spxExpiry?: string | null;
  },
): boolean {
  const matches = (leg: SkewVariant | undefined, ticker: string, delta: number, expiry?: string | null) => {
    if (!leg || String(leg.ticker ?? "").toUpperCase() !== ticker.toUpperCase()) return false;
    if (Number(leg.delta) !== delta || String(leg.timeframe ?? "").toUpperCase() !== expected.timeframe.toUpperCase()) return false;
    return !expiry || (Array.isArray(leg.expiries) && leg.expiries.includes(expiry));
  };
  return matches(payload.nq, expected.nqTicker, expected.nqDelta, expected.nqExpiry)
    && matches(payload.spx, expected.spxTicker, expected.spxDelta, expected.spxExpiry);
}
