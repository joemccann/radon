"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { DiscoverData } from "./types";

const SYNC_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

type UseDiscoverReturn = {
  data: DiscoverData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
  syncNow: () => void;
};

export function useDiscover(active: boolean): UseDiscoverReturn {
  const [data, setData] = useState<DiscoverData | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const didInitialSync = useRef(false);

  const triggerSync = useCallback(async () => {
    setSyncing(true);
    try {
      const res = await fetch("/api/discover", { method: "POST", cache: "no-store" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error ?? "Discover sync failed");
      }
      const json = (await res.json()) as DiscoverData;
      setData(json);
      setLastSync(json.discovery_time || null);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Discover sync failed");
    } finally {
      setSyncing(false);
    }
  }, []);

  const syncNow = useCallback(() => {
    void triggerSync();
  }, [triggerSync]);

  // Initial fetch — read cached file, auto-sync if stale
  useEffect(() => {
    if (!active) return;

    const init = async () => {
      try {
        const res = await fetch("/api/discover", { cache: "no-store" });
        if (!res.ok) throw new Error("Failed to fetch discover data");
        const json = (await res.json()) as DiscoverData;
        setData(json);
        setLastSync(json.discovery_time || null);
        setError(null);
        setLoading(false);

        // Auto-sync on first load
        if (!didInitialSync.current) {
          didInitialSync.current = true;
          void triggerSync();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
        setLoading(false);
        // Still try to sync even if cache read fails
        if (!didInitialSync.current) {
          didInitialSync.current = true;
          void triggerSync();
        }
      }
    };

    void init();
  }, [active, triggerSync]);

  // Auto-sync interval (only when active)
  useEffect(() => {
    if (!active) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }

    intervalRef.current = setInterval(() => {
      void triggerSync();
    }, SYNC_INTERVAL_MS);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [active, triggerSync]);

  return { data, loading, syncing, error, lastSync, syncNow };
}
