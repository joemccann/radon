"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PortfolioData, PortfolioSnapshotSeed } from "./types";
import { readOfflineMeta } from "./offline/offlineStatus";
import {
  reportFetchFailure,
  reportFetchSuccess,
  reportOfflineServed,
} from "./offline/offlineSignals";
import { useRouteRefreshKey } from "./RouteRefreshContext";

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

type UsePortfolioOptions = {
  initialSnapshot?: PortfolioSnapshotSeed;
  includeEntryDates?: boolean;
};

export function usePortfolio(
  active: boolean = true,
  options: UsePortfolioOptions = {},
): UsePortfolioReturn {
  const { initialSnapshot, includeEntryDates = false } = options;
  const endpoint = includeEntryDates
    ? "/api/portfolio?include=entry-dates"
    : "/api/portfolio";
  const [data, setData] = useState<PortfolioData | null>(() => initialSnapshot?.data ?? null);
  const [loading, setLoading] = useState(() => initialSnapshot === undefined);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(() => initialSnapshot?.warning ?? null);
  const [lastSync, setLastSync] = useState<string | null>(() => initialSnapshot?.data.last_sync ?? null);
  const syncingRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollCachedRef = useRef<() => Promise<void>>(async () => {});
  const warningSnapshotRef = useRef<string | null>(
    initialSnapshot?.warning ? initialSnapshot.data.last_sync : null,
  );
  const dataGenerationRef = useRef(0);
  const readingEndpointRef = useRef<string | null>(null);
  const latestReadIdRef = useRef(0);
  const currentEndpointRef = useRef(endpoint);
  const lastReadAtRef = useRef(0);
  const rateLimitedUntilRef = useRef(0);
  const hasDataRef = useRef(initialSnapshot !== undefined);
  const initialLoadStartedRef = useRef(initialSnapshot !== undefined);
  const previousActiveRef = useRef(active);
  const mountedRef = useRef(true);
  const activeRef = useRef(active);
  const routeKey = useRouteRefreshKey();
  const lastRouteKeyRef = useRef(routeKey);
  const lastEndpointRef = useRef(endpoint);
  activeRef.current = active;
  currentEndpointRef.current = endpoint;

  const fetchPortfolio = useCallback(async (): Promise<number | null> => {
    const pendingRateLimitMs = rateLimitedUntilRef.current - Date.now();
    if (pendingRateLimitMs > 0) return pendingRateLimitMs;
    // Deduplicate only an identical read. A route transition from the base
    // snapshot to the /orders entry-date variant must start immediately even
    // if the old endpoint is still resolving.
    if (readingEndpointRef.current === endpoint) return null;
    const readId = latestReadIdRef.current + 1;
    latestReadIdRef.current = readId;
    readingEndpointRef.current = endpoint;
    const generation = dataGenerationRef.current;
    let networkResolved = false;
    let retryDelayMs: number | null = null;
    try {
      const res = await fetch(endpoint, {
        cache: "no-store",
        signal: AbortSignal.timeout(GET_FETCH_TIMEOUT_MS),
      });
      networkResolved = true;
      if (
        readId !== latestReadIdRef.current
        || endpoint !== currentEndpointRef.current
      ) return retryDelayMs;
      if (res.status === 429) {
        const retryAfterSeconds = Number(res.headers.get("Retry-After"));
        if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0) {
          retryDelayMs = Math.min(
            MAX_RATE_LIMIT_BACKOFF_MS,
            Math.max(POLL_INTERVAL_MS, Math.ceil(retryAfterSeconds * 1_000)),
          );
          rateLimitedUntilRef.current = Date.now() + retryDelayMs;
        }
        // A limited cached read is not degradation while a snapshot is on
        // screen; only a cold tab with nothing to show reports it.
        if (hasDataRef.current) return retryDelayMs;
      }
      const meta = readOfflineMeta(res.headers);
      if (meta.servedOffline) reportOfflineServed(meta.cachedAt);
      else reportFetchSuccess();
      if (!res.ok) throw new Error(`Failed to fetch portfolio (${res.status})`);
      const json = (await res.json()) as PortfolioData;
      if (
        !mountedRef.current
        || readId !== latestReadIdRef.current
        || endpoint !== currentEndpointRef.current
        || generation !== dataGenerationRef.current
      ) return retryDelayMs;
      hasDataRef.current = true;
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
      const isCurrentRead = readId === latestReadIdRef.current
        && endpoint === currentEndpointRef.current;
      if (!networkResolved && isCurrentRead) reportFetchFailure();
      if (
        mountedRef.current
        && isCurrentRead
        && generation === dataGenerationRef.current
      ) {
        setError(err instanceof Error ? err.message : "Unknown error");
      }
    } finally {
      if (readId === latestReadIdRef.current) {
        readingEndpointRef.current = null;
        lastReadAtRef.current = Date.now();
        if (mountedRef.current && endpoint === currentEndpointRef.current) {
          setLoading(false);
        }
      }
    }
    return retryDelayMs;
  }, [endpoint]);

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
      // A sibling tab or device already spent this window's producer budget;
      // its snapshot arrives on the next poll (a separate, unlimited-so-far
      // read bucket, so the poll is deliberately NOT backed off here).
      if (res.status === 429) return;
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error ?? "Sync failed");
      }
      const json = (await res.json()) as PortfolioData;
      if (!mountedRef.current || syncGeneration !== dataGenerationRef.current) return;
      dataGenerationRef.current += 1;
      hasDataRef.current = true;
      setData((current) => includeEntryDates
        ? {
            ...json,
            trade_log_dates: current?.trade_log_dates,
            contract_open_dates: current?.contract_open_dates,
          }
        : json);
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
  }, [includeEntryDates]);

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

  // `WorkspaceShell` survives client navigation. The endpoint changes when
  // entering or leaving /orders even if the hook instance does not remount;
  // fetch that exact variant immediately and let the request-id guard discard
  // any older base response still in flight.
  useEffect(() => {
    if (endpoint === lastEndpointRef.current) return;
    lastEndpointRef.current = endpoint;
    void fetchPortfolio().then((retryDelayMs) => {
      if (retryDelayMs !== null && activeRef.current) scheduleNext(retryDelayMs);
    });
  }, [endpoint, fetchPortfolio, scheduleNext]);

  // Client navigations often keep this hook mounted (same shell instance).
  // The mount GET never re-runs, so without a pathname trigger the next
  // snapshot waits for the 30s poll — the latent delay on every route change.
  useEffect(() => {
    if (!routeKey || routeKey === lastRouteKeyRef.current) return;
    lastRouteKeyRef.current = routeKey;
    void fetchPortfolio().then((retryDelayMs) => {
      if (retryDelayMs !== null && activeRef.current) scheduleNext(retryDelayMs);
    });
  }, [routeKey, fetchPortfolio, scheduleNext]);

  useEffect(() => {
    const becameActive = active && !previousActiveRef.current;
    previousActiveRef.current = active;

    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (active) {
      const freshReadInFlight = readingEndpointRef.current !== null;
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
