"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type StrikesCache = Map<string, number[]>;

const PREFETCH_INITIAL_DELAY_MS = 3000; // wait for selected expiry to load first
const PREFETCH_DELAY_MS = 1500; // stagger between prefetch requests (IB pacing)
const PREFETCH_CONCURRENCY = 1; // serial to avoid overwhelming IB Gateway

/**
 * Prefetches option chain strikes for all expirations in the background.
 * Returns a cache map and a lookup function.
 *
 * - The selected expiry is fetched eagerly (not by this hook — the caller handles it).
 * - All other expirations are fetched in the background with staggered timing.
 * - Results are cached in a Map<expiry, strikes[]> for instant switching.
 */
export function useChainPrefetch(
  ticker: string,
  expirations: string[],
  selectedExpiry: string | null,
) {
  const cacheRef = useRef<StrikesCache>(new Map());
  const [prefetchedCount, setPrefetchedCount] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  // Reset cache when ticker changes
  useEffect(() => {
    cacheRef.current = new Map();
    setPrefetchedCount(0);
  }, [ticker]);

  // Cache a result (called by the main chain fetch too)
  const cacheStrikes = useCallback((expiry: string, strikes: number[]) => {
    cacheRef.current.set(expiry, strikes);
    setPrefetchedCount(cacheRef.current.size);
  }, []);

  // Lookup cached strikes
  const getCachedStrikes = useCallback((expiry: string): number[] | null => {
    return cacheRef.current.get(expiry) ?? null;
  }, []);

  // Background prefetch of non-selected expirations
  useEffect(() => {
    if (expirations.length <= 1 || !selectedExpiry) return;

    // Cancel any in-flight prefetch
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const toFetch = expirations.filter(
      (exp) => exp !== selectedExpiry && !cacheRef.current.has(exp),
    );

    if (toFetch.length === 0) return;

    // Staggered prefetch with concurrency limit
    let idx = 0;

    async function fetchNext(): Promise<void> {
      // Wait for the selected expiry to load first before prefetching others
      await new Promise((r) => setTimeout(r, PREFETCH_INITIAL_DELAY_MS));
      if (controller.signal.aborted) return;

      while (idx < toFetch.length && !controller.signal.aborted) {
        const expiry = toFetch[idx++];
        try {
          // Stagger to avoid IB pacing violations
          if (idx > 1) {
            await new Promise((r) => setTimeout(r, PREFETCH_DELAY_MS));
          }
          if (controller.signal.aborted) return;

          const res = await fetch(
            `/api/options/chain?symbol=${encodeURIComponent(ticker)}&expiry=${expiry}`,
            { signal: controller.signal, cache: "no-store" },
          );
          if (!res.ok) continue;
          const data = await res.json();
          if (data.strikes && !controller.signal.aborted) {
            cacheRef.current.set(expiry, data.strikes);
            setPrefetchedCount(cacheRef.current.size);
          }
        } catch {
          // Aborted or network error — skip silently
        }
      }
    }

    // Launch concurrent workers
    const workers: Promise<void>[] = [];
    for (let i = 0; i < Math.min(PREFETCH_CONCURRENCY, toFetch.length); i++) {
      workers.push(fetchNext());
    }

    return () => {
      controller.abort();
    };
  }, [ticker, expirations, selectedExpiry]);

  return {
    cacheStrikes,
    getCachedStrikes,
    prefetchedCount,
    totalExpirations: expirations.length,
  };
}
