"use client";

import { createContext, useContext, useRef, type ReactNode } from "react";

export type ReturnCacheEntry<T> = {
  data: T;
  fetchedAt: number;
  lastSync: string | null;
};

/**
 * Tab-lifetime cache for GET snapshots. A remount inside the poll window
 * reads this instead of hitting the network. Failures never write, so a
 * 502 cannot evict the last good payload.
 */
export type ReturnCache = {
  /** Last successful snapshot, including one past the poll window. */
  read<T>(key: string): ReturnCacheEntry<T> | null;
  /** Dropped when `generation` predates the latest identity purge. */
  write<T>(key: string, entry: ReturnCacheEntry<T>, generation: number): void;
};

export function isReturnCacheFresh(
  entry: ReturnCacheEntry<unknown> | null,
  maxAgeMs: number,
  now = Date.now(),
): boolean {
  if (!entry) return false;
  if (!Number.isFinite(maxAgeMs)) return true;
  return now - entry.fetchedAt < maxAgeMs;
}

const ReturnCacheContext = createContext<ReturnCache | null>(null);

// Every live store, so an account change can drop the previous user's snapshots.
const liveStores = new Set<Map<string, ReturnCacheEntry<unknown>>>();
let identityGeneration = 0;

export function purgeReturnCaches(): void {
  identityGeneration += 1;
  for (const store of liveStores) store.clear();
}

/** Generation captured by async producers so a pre-purge response cannot repopulate a new identity's cache. */
export function getReturnCacheGeneration(): number {
  return identityGeneration;
}

export function createReturnCache(): ReturnCache {
  const store = new Map<string, ReturnCacheEntry<unknown>>();
  liveStores.add(store);
  return {
    read(key) {
      const entry = store.get(key);
      return entry ? entry as ReturnCacheEntry<never> : null;
    },
    write(key, entry, generation) {
      if (generation !== identityGeneration) return;
      store.set(key, entry);
    },
  };
}

export function ReturnCacheProvider({ children }: { children: ReactNode }) {
  const cache = useRef<ReturnCache | null>(null);
  if (cache.current === null) cache.current = createReturnCache();
  return (
    <ReturnCacheContext.Provider value={cache.current}>
      {children}
    </ReturnCacheContext.Provider>
  );
}

export function useReturnCache(): ReturnCache | null {
  return useContext(ReturnCacheContext);
}
