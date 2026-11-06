/**
 * Staleness check for internals skew history cache.
 *
 * The UW risk-reversal skew API publishes end-of-day data after market close.
 * readLongRangeSkewHistory() previously blocked all fresh fetches when the
 * market was closed, causing stale data to persist across sessions.  This
 * helper lets the caller decide whether the last response held in memory is
 * still fresh enough to serve without hitting the FastAPI backend.
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
