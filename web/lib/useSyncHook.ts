"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { readOfflineMeta } from "./offline/offlineStatus";
import {
  reportFetchFailure,
  reportFetchSuccess,
  reportOfflineServed,
} from "./offline/offlineSignals";
import { useRouteRefreshKey } from "./RouteRefreshContext";
import { resolveRetryDelayMs } from "./syncRetrySchedule";

const DEFAULT_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes
/** Backoff ceiling and attempt cap for the stale-data retry. R-230. */
const DEFAULT_MAX_RETRY_DELAY_MS = 60_000;
const DEFAULT_MAX_RETRY_ATTEMPTS = 6;
type RetryMethod = "GET" | "POST";

type UseSyncConfig<T> = {
  endpoint: string;
  interval?: number;
  hasPost?: boolean; // default true; false = GET-only polling
  extractTimestamp?: (data: T) => string | null;
  shouldRetry?: (data: T) => boolean;
  retryIntervalMs?: number;
  /** Ceiling for the retry backoff. R-230. */
  maxRetryDelayMs?: number;
  /** Give up after this many retries and fall back to the poll interval. */
  maxRetryAttempts?: number;
  retryMethod?: RetryMethod;
  showBackgroundError?: boolean;
  /**
   * When false, skip the mount-time GET until `active` becomes true.
   * Default true (legacy: inactive still hydrates cache for visible panels).
   * Scanner mode hooks set this false so cold /scanner only fetches the active mode (T7).
   */
  loadWhenInactive?: boolean;
};

export type UseSyncReturn<T> = {
  data: T | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
  syncNow: () => void;
};

/**
 * Above the proxy's own upstream budget so the route's structured timeout
 * error normally arrives first and this signal is the backstop (the
 * `radonFetchText` / preferences shape, REL-038 R-083).
 */
const SYNC_REQUEST_TIMEOUT_MS = 20_000;

export function useSyncHook<T>(config: UseSyncConfig<T>, active: boolean): UseSyncReturn<T> {
  const {
    endpoint,
    interval = DEFAULT_INTERVAL_MS,
    hasPost = true,
    extractTimestamp,
    shouldRetry,
    retryIntervalMs = 0,
    maxRetryDelayMs = DEFAULT_MAX_RETRY_DELAY_MS,
    maxRetryAttempts = DEFAULT_MAX_RETRY_ATTEMPTS,
    retryMethod = "POST",
    showBackgroundError = false,
    loadWhenInactive = true,
  } = config;
  const isDemoMode = process.env.NEXT_PUBLIC_RADON_DEMO === "1";

  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Retries issued for the current stale streak; reset on a fresh payload. */
  const retryAttemptRef = useRef(0);
  /** R-106: one request per verb at a time. */
  const inFlightRef = useRef<Set<RetryMethod>>(new Set());
  const didInitialSync = useRef(false);
  const didInitialRead = useRef(false);
  const initialLoadKeyRef = useRef<string | null>(null);
  const previousActiveRef = useRef(active);
  const routeKey = useRouteRefreshKey();
  const lastRouteKeyRef = useRef(routeKey);
  const requestRef = useRef<(method: RetryMethod, background?: boolean) => Promise<void>>(async () => {});

  const clearRetry = useCallback(() => {
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current);
      retryTimeoutRef.current = null;
    }
  }, []);

  /**
   * Arm the next stale-data retry, or stop.
   *
   * The streak counter is what makes this bounded: a payload that no longer
   * needs a retry resets it, and a payload that always needs one walks the
   * backoff ladder and then gives up, leaving the normal poll interval to
   * pick up the recovery. R-230.
   */
  const armRetry = useCallback((json: T) => {
    if (isDemoMode || !active || !shouldRetry?.(json)) {
      retryAttemptRef.current = 0;
      return;
    }
    const delay = resolveRetryDelayMs({
      baseMs: retryIntervalMs,
      attempt: retryAttemptRef.current,
      maxDelayMs: maxRetryDelayMs,
      maxAttempts: maxRetryAttempts,
    });
    if (delay === null) return;
    retryAttemptRef.current += 1;
    retryTimeoutRef.current = setTimeout(() => {
      void requestRef.current(retryMethod, true);
    }, delay);
  }, [active, isDemoMode, shouldRetry, retryIntervalMs, maxRetryDelayMs, maxRetryAttempts, retryMethod]);

  const executeRequest = useCallback(async (method: RetryMethod, background = false) => {
    // R-106: a wedged endpoint (FastAPI blocked on UW / MenthorQ) used to
    // accumulate one hung request per interval tick, per mounted hook,
    // indefinitely — and the browser's 6-connection/host limit then
    // head-of-line-blocked every other API call in the tab. `usePortfolio`
    // and `useOrders` already do both of these; this hook did neither.
    if (inFlightRef.current.has(method)) return;
    inFlightRef.current.add(method);
    if (!background && method === "POST") {
      setSyncing(true);
    }
    let networkResolved = false;
    try {
      const res = await fetch(endpoint, {
        method,
        cache: "no-store",
        signal: AbortSignal.timeout(SYNC_REQUEST_TIMEOUT_MS),
      });
      networkResolved = true;
      const meta = readOfflineMeta(res.headers);
      if (meta.servedOffline) reportOfflineServed(meta.cachedAt);
      else reportFetchSuccess();
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error ?? `Sync failed (${res.status})`);
      }
      const json = (await res.json()) as T;
      setData(json);
      setLastSync(extractTimestamp ? extractTimestamp(json) : new Date().toISOString());
      setError(null);

      clearRetry();
      armRetry(json);
    } catch (err) {
      if (!networkResolved) reportFetchFailure();
      // Only show error if we don't already have valid cached data —
      // unless the caller explicitly wants the stale view marked as degraded.
      setData((prev) => {
        if (!prev || showBackgroundError) {
          setError(err instanceof Error ? err.message : "Sync failed");
        }
        return prev;
      });
    } finally {
      inFlightRef.current.delete(method);
      if (!background && method === "POST") {
        setSyncing(false);
      }
    }
  }, [armRetry, clearRetry, endpoint, extractTimestamp, showBackgroundError]);

  requestRef.current = executeRequest;

  const triggerSync = useCallback(async () => {
    // Demo snapshots are read-only. A manual refresh re-reads the deterministic
    // fixture instead of consuming a producer/mutation quota.
    const method = hasPost && !isDemoMode ? "POST" : "GET";
    await executeRequest(method, false);
  }, [executeRequest, hasPost, isDemoMode]);

  // Initial fetch — read the cached file once when the hook mounts (unless
  // loadWhenInactive is false and the consumer is inactive). active=false
  // still disables polling; by default it does not blank a visible panel.
  useEffect(() => {
    if (!active && !loadWhenInactive) {
      // Inactive + no prefetch: leave data null; badges stay empty until load.
      if (!didInitialRead.current) {
        setLoading(false);
      }
      return;
    }
    if (initialLoadKeyRef.current === endpoint) return;
    initialLoadKeyRef.current = endpoint;

    const init = async () => {
      let networkResolved = false;
      try {
        if (!didInitialRead.current) setLoading(true);
        const res = await fetch(endpoint, {
          method: "GET",
          cache: "no-store",
          signal: AbortSignal.timeout(SYNC_REQUEST_TIMEOUT_MS),
        });
        networkResolved = true;
        const meta = readOfflineMeta(res.headers);
        if (meta.servedOffline) reportOfflineServed(meta.cachedAt);
        else reportFetchSuccess();
        if (!res.ok) throw new Error("Failed to fetch cached data");
        const json = (await res.json()) as T;
        setData(json);
        setLastSync(extractTimestamp ? extractTimestamp(json) : null);
        setError(null);
        setLoading(false);
        didInitialRead.current = true;

        clearRetry();
        armRetry(json);

        // Auto-sync on first load when the hook is active. GET-only endpoints
        // already hydrated above — do not immediately re-GET the same cache.
        if (!isDemoMode && active && !didInitialSync.current) {
          didInitialSync.current = true;
          if (hasPost) void triggerSync();
        }
      } catch (err) {
        if (!networkResolved) reportFetchFailure();
        setError(err instanceof Error ? err.message : "Unknown error");
        setLoading(false);
        didInitialRead.current = true;
        if (!isDemoMode && active && !didInitialSync.current) {
          didInitialSync.current = true;
          if (hasPost) void triggerSync();
        }
      }
    };

    void init();
  }, [active, armRetry, clearRetry, endpoint, hasPost, isDemoMode, loadWhenInactive, triggerSync, extractTimestamp]);

  // If the hook mounted while inactive (with loadWhenInactive), issue the first
  // POST/sync when it later becomes active. When loadWhenInactive is false the
  // initial-load effect above also re-runs on active and performs the GET.
  // Re-activation after the first sync (scanner mode, section still mounted)
  // must kick a fresh producer run — didInitialSync used to swallow that.
  useEffect(() => {
    if (isDemoMode) return;
    const becameActive = active && !previousActiveRef.current;
    previousActiveRef.current = active;
    if (!becameActive || !didInitialRead.current) return;
    didInitialSync.current = true;
    if (hasPost) void triggerSync();
    else void requestRef.current("GET", true);
  }, [active, hasPost, isDemoMode, triggerSync]);

  // Pathname change while this hook stays mounted (regime tabs, ticker
  // swaps). Re-read the cache immediately; do not wait for the interval.
  useEffect(() => {
    if (isDemoMode) return;
    if (!routeKey || routeKey === lastRouteKeyRef.current) return;
    lastRouteKeyRef.current = routeKey;
    if (!didInitialRead.current) return;
    if (!active && !loadWhenInactive) return;
    void requestRef.current("GET", true);
  }, [routeKey, active, isDemoMode, loadWhenInactive]);

  // Auto-sync interval (only when active)
  useEffect(() => {
    if (isDemoMode || !active || interval <= 0) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      clearRetry();
      return;
    }

    intervalRef.current = setInterval(() => {
      void triggerSync();
    }, interval);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      clearRetry();
    };
  }, [active, clearRetry, interval, isDemoMode, triggerSync]);

  const syncNow = useCallback(() => {
    void triggerSync();
  }, [triggerSync]);

  return { data, loading, syncing, error, lastSync, syncNow };
}
