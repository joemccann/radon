/**
 * Pure cache-decision helpers for the /api/ticker/info route.
 *
 * Extracted so the "don't cache empty results" invariant
 * (feedback_dont_cache_empty_results) is unit-testable without the route's
 * filesystem + network machinery. A single transient Unusual Whales / Exa
 * failure must never poison a ticker's company data for the full 24h stats
 * TTL — these helpers gate cache reuse and persistence on real payload
 * content, not just on the TTL.
 */

export type RecordMap = Record<string, unknown>;

/** True when a record actually carries data (at least one key). */
export function isPopulated(obj: RecordMap | null | undefined): boolean {
  return !!obj && Object.keys(obj).length > 0;
}

/**
 * Whether the cached `uw_info` may be reused instead of re-fetching from UW.
 *
 * Reuse requires BOTH an unexpired stats window AND a non-empty cached payload.
 * The bug this closes: gating reuse on `statsCached` alone re-served an empty
 * `{}` (from a prior UW hiccup) for 24h even while UW was healthy.
 */
export function canReuseUwInfo(cachedUwInfo: RecordMap | null | undefined, statsCached: boolean): boolean {
  return statsCached && isPopulated(cachedUwInfo);
}

/**
 * Choose the uw_info to serve/persist: prefer a freshly-fetched payload, but
 * never downgrade a populated cache to empty on a transient failure.
 */
export function pickUwInfo(
  fetched: RecordMap,
  cachedUwInfo: RecordMap | null | undefined,
): RecordMap {
  return isPopulated(fetched) ? fetched : (cachedUwInfo ?? {});
}

/**
 * Whether a ticker-info payload is worth persisting behind the 24h TTL. An
 * all-empty result (UW + Exa + stats all blank) must NOT be written — that is
 * the poisoning that strands a ticker at "---" until the window expires.
 */
export function hasAnyTickerData(uwInfo: RecordMap, exaProfile: RecordMap, exaStats: RecordMap): boolean {
  return isPopulated(uwInfo) || isPopulated(exaProfile) || isPopulated(exaStats);
}

/** HIT-path stock-state refresh window. uw_info stays on the 24h stats reuse rule. */
export const STOCK_STATE_TTL_MS = 15 * 60 * 1000;

export type StockStateStamp = {
  stock_state_checked_at?: string;
  fetched_at?: string;
};

/**
 * Whether a cache HIT should re-fetch UW stock-state.
 *
 * Prefer `stock_state_checked_at`; fall back to `fetched_at` so legacy entries
 * written before the stamp still honor the 15-minute window instead of
 * refreshing on every request.
 */
export function stockStateRefreshDue(
  entry: StockStateStamp | null | undefined,
  now: number = Date.now(),
): boolean {
  if (!entry) return true;
  const stamp = entry.stock_state_checked_at ?? entry.fetched_at;
  if (!stamp) return true;
  const ts = new Date(stamp).getTime();
  if (Number.isNaN(ts)) return true;
  return now - ts >= STOCK_STATE_TTL_MS;
}
