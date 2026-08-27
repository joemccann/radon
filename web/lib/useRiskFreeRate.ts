"use client";

import { useEffect, useSyncExternalStore } from "react";

let cachedRate: number | null = null;
let cachedAt = 0;
let inFlight: Promise<void> | null = null;
let storeGeneration = 0;
const listeners = new Set<() => void>();
const CLIENT_CACHE_TTL_MS = 24 * 60 * 60 * 1000;
/**
 * Retry ladder for an UNRESOLVED rate. Without one, `loadRiskFreeRate` fired
 * only from a `useEffect(..., [])` per consumer mount and short-circuited on
 * `inFlight`, so on a long-lived WorkspaceShell session a single transient
 * FRED miss at page load pinned the rate for the rest of the session. R-229.
 */
const RETRY_BASE_MS = 60_000;
const RETRY_MAX_MS = 15 * 60_000;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
let retryAttempt = 0;

function clearRetry() {
  if (retryTimer !== null) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }
  retryAttempt = 0;
}

function scheduleRetry() {
  if (retryTimer !== null) return;
  const delay = Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** retryAttempt);
  retryAttempt += 1;
  retryTimer = setTimeout(() => {
    retryTimer = null;
    void loadRiskFreeRate();
  }, delay);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): number | null {
  return cachedRate;
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
      ) {
        // Not a fresh observation. Keep any last-good value and arm the
        // ladder, because nothing else will retry this session. R-229.
        scheduleRetry();
        return;
      }
      cachedRate = data.rate;
      cachedAt = Date.now();
      clearRetry();
      listeners.forEach((listener) => listener());
    })
    .catch(() => {
      // Preserve any last-good value; the ladder recovers the session.
      scheduleRetry();
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
  clearRetry();
  listeners.clear();
}

/**
 * Test seam: install a resolved rate so a component test can exercise the
 * Black-Scholes columns without stubbing the route. The columns now render
 * "—" while the rate is UNRESOLVED (R-229), which is the point — but that
 * makes an unrelated test's silence about the rate an implicit assertion.
 */
export function seedRiskFreeRateForTests(rate: number) {
  cachedRate = rate;
  cachedAt = Date.now();
  clearRetry();
  listeners.forEach((listener) => listener());
}

export type RiskFreeRateState = {
  /** null until a real FRED:DFF observation lands. */
  rate: number | null;
  /** False while the rate is unknown — NOT the same as a rate of 0. */
  resolved: boolean;
};

/**
 * The rate with its resolution state. Prefer this wherever a 0 would be a
 * meaningful reading: `useRiskFreeRate()` collapses "unknown" onto 0, which
 * is exactly what made a dead FRED indistinguishable from a zero-rate world
 * at every layer of the option-value chain. R-229.
 */
export function useRiskFreeRateState(): RiskFreeRateState {
  const rate = useSyncExternalStore(subscribe, getSnapshot, () => null);

  useEffect(() => {
    void loadRiskFreeRate();
  }, []);

  return { rate, resolved: rate !== null };
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
  return useRiskFreeRateState().rate ?? 0;
}
