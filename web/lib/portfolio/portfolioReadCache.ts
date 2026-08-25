import {
  cachedRead,
  cachedReadResult,
  invalidateCache,
  type CachedReadOpts,
  type CachedReadResult,
} from "@/lib/dbCache";

export const PORTFOLIO_SNAPSHOT_CACHE_KEY = "portfolio:snapshot";
export const PORTFOLIO_TRADE_LOG_DATES_CACHE_KEY = "portfolio:tradeLogDates";
export const PORTFOLIO_CONTRACT_OPEN_DATES_CACHE_KEY = "portfolio:contractOpenDates";

// Browser polling is 30 seconds. A 15-second bound coalesces overlapping tabs
// without hiding a newly published 60-second writer snapshot for a full poll.
export const PORTFOLIO_SNAPSHOT_CACHE_TTL_MS = 15_000;
// Entry dates have day granularity and currently support the /orders share
// card. Keep them warm across two browser poll intervals while that consumer
// is separated from the default /portfolio payload.
export const PORTFOLIO_ENTRY_DATES_CACHE_TTL_MS = 60_000;

export function readCachedPortfolioSnapshot<T>(
  fetcher: () => Promise<T>,
  opts: CachedReadOpts = {},
): Promise<CachedReadResult<T>> {
  return cachedReadResult(
    PORTFOLIO_SNAPSHOT_CACHE_KEY,
    PORTFOLIO_SNAPSHOT_CACHE_TTL_MS,
    fetcher,
    opts,
  );
}

export function readCachedPortfolioTradeLogDates<T>(
  fetcher: () => Promise<T>,
): Promise<T> {
  return cachedRead(
    PORTFOLIO_TRADE_LOG_DATES_CACHE_KEY,
    PORTFOLIO_ENTRY_DATES_CACHE_TTL_MS,
    fetcher,
  );
}

export function readCachedPortfolioContractOpenDates<T>(
  fetcher: () => Promise<T>,
): Promise<T> {
  return cachedRead(
    PORTFOLIO_CONTRACT_OPEN_DATES_CACHE_KEY,
    PORTFOLIO_ENTRY_DATES_CACHE_TTL_MS,
    fetcher,
  );
}

export function invalidatePortfolioSnapshotCache(): void {
  invalidateCache(PORTFOLIO_SNAPSHOT_CACHE_KEY);
}

/** A successful live sync can publish both a new snapshot and journal fills. */
export function invalidatePortfolioReadCaches(): void {
  invalidateCache(PORTFOLIO_SNAPSHOT_CACHE_KEY);
  invalidateCache(PORTFOLIO_TRADE_LOG_DATES_CACHE_KEY);
  invalidateCache(PORTFOLIO_CONTRACT_OPEN_DATES_CACHE_KEY);
}
