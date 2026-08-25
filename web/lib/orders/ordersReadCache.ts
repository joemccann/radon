import { cachedRead, invalidateCache } from "@/lib/dbCache";

/**
 * Process-local coalescing for the polled Turso orders snapshot. HTTP caching
 * remains disabled by the route; this only collapses nearly simultaneous tabs
 * and duplicate shell reads against the same direct-to-cloud source.
 */
export const ORDERS_SNAPSHOT_CACHE_KEY = "orders:snapshot";
export const ORDERS_SNAPSHOT_CACHE_TTL_MS = 2_000;

export function readCachedOrdersSnapshot<T>(fetcher: () => Promise<T>): Promise<T> {
  return cachedRead(
    ORDERS_SNAPSHOT_CACHE_KEY,
    ORDERS_SNAPSHOT_CACHE_TTL_MS,
    fetcher,
  );
}

/** Mutating order routes call this before reading back authoritative state. */
export function invalidateOrdersSnapshotCache(): void {
  invalidateCache(ORDERS_SNAPSHOT_CACHE_KEY);
}
