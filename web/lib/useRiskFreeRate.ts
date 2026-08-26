"use client";

import { useEffect, useSyncExternalStore } from "react";

let cachedRate: number | null = null;
let cachedAt = 0;
let inFlight: Promise<void> | null = null;
let storeGeneration = 0;
const listeners = new Set<() => void>();
const CLIENT_CACHE_TTL_MS = 24 * 60 * 60 * 1000;

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot() {
  return cachedRate ?? 0;
}

function loadRiskFreeRate() {
  const cacheIsFresh = cachedRate != null && Date.now() - cachedAt < CLIENT_CACHE_TTL_MS;
  if (cacheIsFresh || inFlight) return inFlight;

  const generation = storeGeneration;
  let request: Promise<void>;
  request = fetch("/api/risk-free-rate", { cache: "no-store" })
    .then((response) => (response.ok ? response.json() : null))
    .then((data: { rate?: number; source?: string; stale?: boolean } | null) => {
      if (generation !== storeGeneration) return;
      // The route deliberately answers transient FRED failures with a usable
      // `rate: 0` fallback. That response is not a fresh observation: caching
      // it for 24 hours would turn a brief upstream failure into a day of
      // incorrect option inputs. Keep any last-good value and leave the cache
      // expired so a later consumer can retry.
      if (
        !data
        || data.source !== "FRED:DFF"
        || data.stale !== false
        || typeof data.rate !== "number"
        || !Number.isFinite(data.rate)
      ) return;
      cachedRate = data.rate;
      cachedAt = Date.now();
      listeners.forEach((listener) => listener());
    })
    .catch(() => {
      // Preserve a last-good value (or the initial 0). A later mount retries.
    })
    .finally(() => {
      if (inFlight === request) inFlight = null;
    });
  inFlight = request;

  return inFlight;
}

/** Test isolation seam for the module-level external store. */
export function resetRiskFreeRateCacheForTests() {
  storeGeneration += 1;
  cachedRate = null;
  cachedAt = 0;
  inFlight = null;
  listeners.clear();
}

/**
 * Latest effective Fed Funds rate (FRED:DFF) as a decimal — used as `r`
 * in Black-Scholes implied-value calculations. Returns 0 until the fetch
 * resolves, so consumers always have a usable number.
 *
 * All mounted consumers share one no-store request and a bounded 24h client
 * cache. An expired value remains renderable while one revalidation runs.
 */
export function useRiskFreeRate(): number {
  const rate = useSyncExternalStore(subscribe, getSnapshot, () => 0);

  useEffect(() => {
    void loadRiskFreeRate();
  }, []);

  return rate;
}
