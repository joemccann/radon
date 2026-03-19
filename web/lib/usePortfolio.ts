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

export function usePortfolio(): UsePortfolioReturn {
  const [data, setData] = useState<PortfolioData | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<string | null>(null);
  const syncingRef = useRef(false);
  const intervalRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const backoffRef = useRef(BASE_INTERVAL_MS);

  const fetchPortfolio = useCallback(async () => {
    try {
      const res = await fetch("/api/portfolio");
      if (!res.ok) throw new Error("Failed to fetch portfolio");
      const json = (await res.json()) as PortfolioData;
      setData(json);
      setLastSync(json.last_sync);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  const scheduleNext = useCallback((delay: number) => {
    if (intervalRef.current) clearTimeout(intervalRef.current);
    intervalRef.current = setTimeout(() => {
      void doSync();
    }, delay);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const doSync = useCallback(async () => {
    if (syncingRef.current) return; // skip if already in-flight
    syncingRef.current = true;
    setSyncing(true);
    try {
      const res = await fetch("/api/portfolio", { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error ?? "Sync failed");
      }
      const json = (await res.json()) as PortfolioData;
      setData(json);
      setLastSync(json.last_sync);
      setError(null);
      // Reset backoff on success
      backoffRef.current = BASE_INTERVAL_MS;
      scheduleNext(BASE_INTERVAL_MS);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
      // Exponential backoff on failure, capped at MAX
      backoffRef.current = Math.min(backoffRef.current * 2, MAX_INTERVAL_MS);
      scheduleNext(backoffRef.current);
    } finally {
      syncingRef.current = false;
      setSyncing(false);
    }
  }, [scheduleNext]);

  const syncNow = useCallback(() => {
    backoffRef.current = BASE_INTERVAL_MS; // reset backoff on manual sync
    void doSync();
  }, [doSync]);

  // Initial fetch (GET cached file), then start sync loop
  useEffect(() => {
    void fetchPortfolio().then(() => {
      scheduleNext(BASE_INTERVAL_MS);
    });
    return () => {
      if (intervalRef.current) clearTimeout(intervalRef.current);
    };
  }, [fetchPortfolio, scheduleNext]);

  // Reset backoff & force sync when tab becomes visible again.
  // Prevents stale data when user returns after FastAPI outage
  // pushed backoff to 5 min.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        backoffRef.current = BASE_INTERVAL_MS;
        if (!syncingRef.current) {
          scheduleNext(500); // sync almost immediately
        }
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [scheduleNext]);

  return { data, loading, syncing, error, lastSync, syncNow };
}
