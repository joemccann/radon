// In-process read-through TTL cache for hot, high-frequency DB reads.
//
// radon-nextjs is a single long-lived Node process, so module-level state is
// shared across requests. Many browser tabs + components poll the same routes
// (portfolio / service-health / gex) every ~60s, each otherwise paying the
// ~30ms Turso Hrana round-trip floor. This coalesces repeated identical reads
// into ONE round trip per TTL window; cache hits return in <1ms.
//
// Two coalescing mechanisms:
//   1. TTL — a value is served directly while fresh (now < freshUntil).
//   2. Single-flight — concurrent misses for the same key share one in-flight
//      fetch instead of each hitting Turso.
//
// Default behaviour is a pure TTL cache: a fetch error propagates (the caller's
// existing fallback/self-heal path runs). `staleWhileError` opts into serving
// the last good value during a brief Turso outage (bounded by `maxStaleMs`) —
// for UX-tolerant reads (portfolio/gex), NOT the health signal itself.
//
// This is NOT the retired embedded replica (see
// feedback_libsql_replica_one_writer): no local SQLite file, no WAL, no write
// path — just a memory cache over the direct-to-cloud read. Zero WAL-conflict
// surface.

type Entry<T> = { value: T; freshUntil: number; storedAt: number };

const store = new Map<string, Entry<unknown>>();
const inflight = new Map<string, Promise<unknown>>();

const DEFAULT_MAX_STALE_MS = 60_000;

export interface CachedReadOpts {
  /** Serve the last good value if the refresh fetch throws (e.g. a Turso
   * HTTP-pipeline stall), bounded by `maxStaleMs`. Off by default. */
  staleWhileError?: boolean;
  /** Upper bound on how old a value may be to satisfy `staleWhileError`. */
  maxStaleMs?: number;
}

/**
 * Read `key` through the cache. Returns the cached value while fresh; otherwise
 * runs `fetcher` (single-flighted across concurrent callers), caches the result
 * for `ttlMs`, and returns it.
 */
export async function cachedRead<T>(
  key: string,
  ttlMs: number,
  fetcher: () => Promise<T>,
  opts: CachedReadOpts = {},
): Promise<T> {
  // Bypass entirely under test so route handlers (which mock @/lib/db and call
  // their GET repeatedly) see a fresh read each call. The cache's own unit test
  // sets RADON_DB_CACHE_FORCE=1 to exercise real caching. Mirrors the
  // NODE_ENV!=="test" replica guard in db.ts.
  if (process.env.NODE_ENV === "test" && process.env.RADON_DB_CACHE_FORCE !== "1") {
    return fetcher();
  }

  const now = Date.now();
  const hit = store.get(key) as Entry<T> | undefined;
  if (hit && now < hit.freshUntil) return hit.value;

  const existing = inflight.get(key) as Promise<T> | undefined;
  if (existing) return existing;

  const fetchPromise = (async () => {
    const value = await fetcher();
    store.set(key, { value, freshUntil: Date.now() + ttlMs, storedAt: Date.now() });
    return value;
  })();
  inflight.set(key, fetchPromise);

  try {
    return await fetchPromise;
  } catch (err) {
    if (opts.staleWhileError && hit) {
      const maxStale = opts.maxStaleMs ?? DEFAULT_MAX_STALE_MS;
      if (Date.now() - hit.storedAt <= maxStale) return hit.value;
    }
    throw err;
  } finally {
    inflight.delete(key);
  }
}

/** Drop one cache key (e.g. after a write that invalidates it). */
export function invalidateCache(key: string): void {
  store.delete(key);
}

/** Test seam — clear all cached entries + in-flight tracking. */
export function __clearDbCache(): void {
  store.clear();
  inflight.clear();
}
