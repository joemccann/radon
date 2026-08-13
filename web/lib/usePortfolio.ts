"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PortfolioData } from "./types";
import { readOfflineMeta } from "./offline/offlineStatus";
import {
  reportFetchFailure,
  reportFetchSuccess,
  reportOfflineServed,
} from "./offline/offlineSignals";

const POLL_INTERVAL_MS = 30_000;
const MAX_RATE_LIMIT_BACKOFF_MS = 15 * 60_000;
const GET_FETCH_TIMEOUT_MS = 12_000;
const POST_FETCH_TIMEOUT_MS = 42_000;

type UsePortfolioReturn = {
  data: PortfolioData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
  syncNow: () => void;
};

export function usePortfolio(active: boolean = true): UsePortfolioReturn {
  const [data, setData] = useState<PortfolioData | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<string | null>(null);
  const syncingRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollCachedRef = useRef<() => Promise<void>>(async () => {});
  const warningSnapshotRef = useRef<string | null>(null);
  const dataGenerationRef = useRef(0);
  const readingRef = useRef(false);
  const lastReadAtRef = useRef(0);
  const rateLimitedUntilRef = useRef(0);
  const initialLoadStartedRef = useRef(false);
  const previousActiveRef = useRef(active);
  const mountedRef = useRef(true);
  const activeRef = useRef(active);
  activeRef.current = active;

  const fetchPortfolio = useCallback(async (): Promise<number | null> => {
    const pendingRateLimitMs = rateLimitedUntilRef.current - Date.now();
    if (pendingRateLimitMs > 0) return pendingRateLimitMs;
    if (readingRef.current) return null;
    readingRef.current = true;
    const generation = dataGenerationRef.current;
    let networkResolved = false;
    let retryDelayMs: number | null = null;
    try {
      const res = await fetch("/api/portfolio", {
        cache: "no-store",
        signal: AbortSignal.timeout(GET_FETCH_TIMEOUT_MS),
      });
      networkResolved = true;
      if (res.status === 429) {
        const retryAfterSeconds = Number(res.headers.get("Retry-After"));
        if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0) {
          retryDelayMs = Math.min(
            MAX_RATE_LIMIT_BACKOFF_MS,
            Math.max(POLL_INTERVAL_MS, Math.ceil(retryAfterSeconds * 1_000)),
          );
          rateLimitedUntilRef.current = Date.now() + retryDelayMs;
        }
      }
      const meta = readOfflineMeta(res.headers);
      if (meta.servedOffline) reportOfflineServed(meta.cachedAt);
      else reportFetchSuccess();
      if (!res.ok) throw new Error(`Failed to fetch portfolio (${res.status})`);
      const json = (await res.json()) as PortfolioData;
      if (!mountedRef.current || generation !== dataGenerationRef.current) return retryDelayMs;
      setData(json);
      setLastSync(json.last_sync);
      const warning = res.headers.get("X-Sync-Warning");
      if (warning) {
        warningSnapshotRef.current = json.last_sync;
        setError(warning);
        return retryDelayMs;
      }
      warningSnapshotRef.current = null;
      rateLimitedUntilRef.current = 0;
      setError(null);
    } catch (err) {
      if (!networkResolved) reportFetchFailure();
      if (mountedRef.current && generation === dataGenerationRef.current) {
        setError(err instanceof Error ? err.message : "Unknown error");
      }
    } finally {
      readingRef.current = false;
      lastReadAtRef.current = Date.now();
      if (mountedRef.current) setLoading(false);
    }
    return retryDelayMs;
  }, []);

  const scheduleNext = useCallback((delay: number) => {
    if (!mountedRef.current || !activeRef.current) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    const rateLimitDelay = Math.max(0, rateLimitedUntilRef.current - Date.now());
    timerRef.current = setTimeout(() => {
      void pollCachedRef.current();
    }, Math.max(delay, rateLimitDelay));
  }, []);

  const pollCached = useCallback(async () => {
    const retryDelayMs = await fetchPortfolio();
    if (mountedRef.current && activeRef.current) {
      scheduleNext(retryDelayMs ?? POLL_INTERVAL_MS);
    }
  }, [fetchPortfolio, scheduleNext]);
  pollCachedRef.current = pollCached;

  const doSync = useCallback(async () => {
    if (syncingRef.current) return;
    syncingRef.current = true;
    const syncGeneration = ++dataGenerationRef.current;
    if (mountedRef.current) setSyncing(true);
    try {
      const res = await fetch("/api/portfolio", {
        method: "POST",
        cache: "no-store",
        signal: AbortSignal.timeout(POST_FETCH_TIMEOUT_MS),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error ?? "Sync failed");
      }
      const json = (await res.json()) as PortfolioData;
      if (!mountedRef.current || syncGeneration !== dataGenerationRef.current) return;
      dataGenerationRef.current += 1;
      setData(json);
      setLastSync(json.last_sync);
      const warning = res.headers.get("X-Sync-Warning");
      if (warning) {
        warningSnapshotRef.current = json.last_sync;
        setError(warning);
      } else {
        warningSnapshotRef.current = null;
        setError(null);
      }
    } catch (err) {
      if (mountedRef.current) setError(err instanceof Error ? err.message : "Sync failed");
    } finally {
      syncingRef.current = false;
      if (mountedRef.current) setSyncing(false);
    }
  }, []);

  const syncNow = useCallback(() => {
    void doSync();
  }, [doSync]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Cached snapshot reads are safe to fan out across browser tabs. Live IB
  // synchronization is deliberately reserved for syncNow and server timers.
  useEffect(() => {
    if (initialLoadStartedRef.current) return;
    initialLoadStartedRef.current = true;
    void fetchPortfolio().then((retryDelayMs) => {
      if (retryDelayMs !== null && activeRef.current) scheduleNext(retryDelayMs);
    });
  }, [fetchPortfolio, scheduleNext]);

  useEffect(() => {
    const becameActive = active && !previousActiveRef.current;
    previousActiveRef.current = active;

    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (active) {
      const freshReadInFlight = readingRef.current;
      const recentlyRead = Date.now() - lastReadAtRef.current < POLL_INTERVAL_MS;
      scheduleNext(becameActive && !freshReadInFlight && !recentlyRead ? 500 : POLL_INTERVAL_MS);
    }

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [active, scheduleNext]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      if (active) scheduleNext(500);
      else void fetchPortfolio();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [scheduleNext, active, fetchPortfolio]);

  return { data, loading, syncing, error, lastSync, syncNow };
}
