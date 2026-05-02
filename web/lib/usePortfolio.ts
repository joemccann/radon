"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PortfolioData } from "./types";

const BASE_INTERVAL_MS = 30_000;
const MAX_INTERVAL_MS = 300_000; // 5 min cap on backoff

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
  const intervalRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const backoffRef = useRef(BASE_INTERVAL_MS);
  const didInitialReadRef = useRef(false);
  const initialLoadStartedRef = useRef(false);
  const syncLoopArmedRef = useRef(false);
  const doSyncRef = useRef<() => Promise<void>>(async () => {});

  const fetchPortfolio = useCallback(async () => {
    try {
      const res = await fetch("/api/portfolio", { cache: "no-store" });
      if (!res.ok) throw new Error("Failed to fetch portfolio");
      const json = (await res.json()) as PortfolioData;
      setData(json);
      setLastSync(json.last_sync);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
      didInitialReadRef.current = true;
    }
  }, []);

  const scheduleNext = useCallback((delay: number) => {
    if (!active) return;
    if (intervalRef.current) clearTimeout(intervalRef.current);
    intervalRef.current = setTimeout(() => {
      void doSyncRef.current();
    }, delay);
  }, [active]);

  const doSync = useCallback(async () => {
    if (syncingRef.current) return;
    syncingRef.current = true;
    setSyncing(true);
    try {
      const res = await fetch("/api/portfolio", { method: "POST", cache: "no-store" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error ?? "Sync failed");
      }
      const json = (await res.json()) as PortfolioData;
      setData(json);
      setLastSync(json.last_sync);
      setError(null);
      backoffRef.current = BASE_INTERVAL_MS;
      scheduleNext(BASE_INTERVAL_MS);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
      backoffRef.current = Math.min(backoffRef.current * 2, MAX_INTERVAL_MS);
      scheduleNext(backoffRef.current);
    } finally {
      syncingRef.current = false;
      setSyncing(false);
    }
  }, [scheduleNext]);

  doSyncRef.current = doSync;

  const syncNow = useCallback(() => {
    backoffRef.current = BASE_INTERVAL_MS;
    syncLoopArmedRef.current = true;
    void doSync();
  }, [doSync]);

  // Always read the cached portfolio once on mount. `active=false` only disables
  // polling and background sync so closed-market routes can still render.
  useEffect(() => {
    if (initialLoadStartedRef.current) return;
    initialLoadStartedRef.current = true;

    let cancelled = false;

    const init = async () => {
      await fetchPortfolio();
      if (cancelled) return;
      if (active) {
        syncLoopArmedRef.current = true;
        scheduleNext(BASE_INTERVAL_MS);
      }
    };

    void init();

    return () => {
      cancelled = true;
      if (intervalRef.current) {
        clearTimeout(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [active, fetchPortfolio, scheduleNext]);

  // If the hook mounted while inactive, start syncing the first time it becomes active.
  useEffect(() => {
    if (!active) {
      if (intervalRef.current) {
        clearTimeout(intervalRef.current);
        intervalRef.current = null;
      }
      syncLoopArmedRef.current = false;
      return;
    }

    if (!didInitialReadRef.current || syncLoopArmedRef.current) return;
    syncLoopArmedRef.current = true;
    void doSync();
  }, [active, doSync]);

  // Reset backoff & force sync when tab becomes visible again.
  // Prevents stale data when user returns after FastAPI outage
  // pushed backoff to 5 min.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible" && active) {
        backoffRef.current = BASE_INTERVAL_MS;
        if (!syncingRef.current) {
          syncLoopArmedRef.current = true;
          scheduleNext(500);
        }
      }
    };
    if (active) {
      document.addEventListener("visibilitychange", onVisible);
      return () => document.removeEventListener("visibilitychange", onVisible);
    }
  }, [scheduleNext, active]);

  return { data, loading, syncing, error, lastSync, syncNow };
}
