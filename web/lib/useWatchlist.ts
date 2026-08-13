"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useRef, useState } from "react";

export type WatchlistEntry = {
  id: string;
  symbol: string;
  sector: string | null;
  added_at: string;
};

type UseWatchlistReturn = {
  watchlist: WatchlistEntry[];
  isLoading: boolean;
  isWatched: (symbol: string) => boolean;
  toggleWatch: (symbol: string, sector?: string) => Promise<void>;
};

type UserStore = {
  cache: WatchlistEntry[];
  loaded: boolean;
  inFlight: Promise<void> | null;
  mutation: Promise<void>;
  generation: number;
};

const stores = new Map<string, UserStore>();
const subscribers = new Set<() => void>();

function storeFor(userId: string): UserStore {
  const existing = stores.get(userId);
  if (existing) return existing;
  const created = { cache: [], loaded: false, inFlight: null, mutation: Promise.resolve(), generation: 0 };
  stores.set(userId, created);
  return created;
}

function notify(): void {
  for (const fn of subscribers) fn();
}

function clearUser(userId: string): void {
  const store = stores.get(userId);
  if (store) store.generation += 1;
  stores.delete(userId);
  notify();
}

async function loadWatchlist(userId: string, force = false): Promise<void> {
  const store = storeFor(userId);
  if (force) {
    store.generation += 1;
    store.inFlight = null;
  }
  if (store.inFlight) return store.inFlight;
  const generation = store.generation;
  store.inFlight = (async () => {
    try {
      const res = await fetch("/api/watchlist", { cache: "no-store" });
      if (!res.ok) throw new Error("Failed to fetch watchlist");
      const json = (await res.json()) as { watchlist: WatchlistEntry[] };
      if (stores.get(userId) !== store || store.generation !== generation) return;
      store.cache = Array.isArray(json.watchlist) ? json.watchlist : [];
    } catch {
      // Preserve this identity's last known state only.
    } finally {
      if (stores.get(userId) === store && store.generation === generation) {
        store.loaded = true;
        store.inFlight = null;
        notify();
      }
    }
  })();
  return store.inFlight;
}

export function useWatchlist(): UseWatchlistReturn {
  const { isLoaded: authLoaded, isSignedIn, userId } = useAuth();
  const identity = authLoaded && isSignedIn && userId ? userId : null;
  const previousIdentity = useRef<string | null>(null);
  const [, forceRender] = useState(0);
  const store = identity ? storeFor(identity) : null;

  useEffect(() => {
    const rerender = () => forceRender((value) => value + 1);
    subscribers.add(rerender);
    return () => { subscribers.delete(rerender); };
  }, []);

  useEffect(() => {
    const previous = previousIdentity.current;
    previousIdentity.current = identity;
    if (previous && previous !== identity) clearUser(previous);
    if (identity) {
      const current = storeFor(identity);
      if (!current.loaded && !current.inFlight) void loadWatchlist(identity);
    }
  }, [identity]);

  const watchlist = store?.cache ?? [];
  const isWatched = useCallback((symbol: string) => {
    const normalized = symbol.trim().toUpperCase();
    return identity ? storeFor(identity).cache.some((entry) => entry.symbol === normalized) : false;
  }, [identity]);

  const toggleWatch = useCallback(async (symbol: string, sector?: string) => {
    if (!identity) throw new Error("Sign in required");
    const normalized = symbol.trim().toUpperCase();
    const current = storeFor(identity);
    const operation = current.mutation.then(async () => {
      if (stores.get(identity) !== current) throw new Error("Identity changed");
      const previousEntry = current.cache.find((entry) => entry.symbol === normalized);
      const already = Boolean(previousEntry);
      current.cache = already
        ? current.cache.filter((entry) => entry.symbol !== normalized)
        : [{
            id: `optimistic-${normalized}`,
            symbol: normalized,
            sector: sector ?? null,
            added_at: new Date().toISOString(),
          }, ...current.cache];
      notify();
      try {
        const res = await fetch(
          already ? `/api/watchlist/${encodeURIComponent(normalized)}` : "/api/watchlist",
          {
            method: already ? "DELETE" : "POST",
            cache: "no-store",
            ...(already ? {} : {
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ symbol: normalized, sector }),
            }),
          },
        );
        if (!res.ok) throw new Error("Failed to update watchlist");
        await loadWatchlist(identity, true);
      } catch (error) {
        if (stores.get(identity) === current) {
          current.cache = current.cache.filter((entry) => entry.symbol !== normalized);
          if (previousEntry) current.cache = [previousEntry, ...current.cache];
          notify();
        }
        throw error;
      }
    });
    current.mutation = operation.catch(() => {});
    try {
      await operation;
    } catch (error) {
      if (stores.get(identity) === current) {
        notify();
      }
      throw error;
    }
  }, [identity]);

  return {
    watchlist,
    isLoading: !authLoaded || Boolean(identity && !store?.loaded),
    isWatched,
    toggleWatch,
  };
}
