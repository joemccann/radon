"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { OrdersData } from "./types";

const SYNC_INTERVAL_MS = 30_000;

type UseOrdersReturn = {
  data: OrdersData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
  syncNow: () => void;
  updateData: (data: OrdersData) => void;
};

export function useOrders(active: boolean): UseOrdersReturn {
  const [data, setData] = useState<OrdersData | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const didInitialSync = useRef(false);

  const triggerSync = useCallback(async () => {
    setSyncing(true);
    try {
      const res = await fetch("/api/orders", { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error ?? "Sync failed");
      }
      const json = (await res.json()) as OrdersData;
      setData(json);
      setLastSync(json.last_sync || null);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  }, []);

  const syncNow = useCallback(() => {
    void triggerSync();
  }, [triggerSync]);

  // Always read cached orders on mount (needed by ticker detail modal on any page).
  // Only auto-sync from IB when active (orders page).
  useEffect(() => {
    const init = async () => {
      try {
        const res = await fetch("/api/orders");
        if (!res.ok) throw new Error("Failed to fetch orders");
        const json = (await res.json()) as OrdersData;
        setData(json);
        setLastSync(json.last_sync || null);
        setError(null);
        setLoading(false);

        // Sync fresh from IB on first load of orders page
        if (active && !didInitialSync.current) {
          didInitialSync.current = true;
          void triggerSync();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
        setLoading(false);
      }
    };

    void init();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When orders page becomes active, trigger IB sync if we haven't yet
  useEffect(() => {
    if (active && !didInitialSync.current && data != null) {
      didInitialSync.current = true;
      void triggerSync();
    }
  }, [active, data, triggerSync]);

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

  const updateData = useCallback((newData: OrdersData) => {
    setData(newData);
    setLastSync(newData.last_sync || null);
    setError(null);
  }, []);

  return { data, loading, syncing, error, lastSync, syncNow, updateData };
}
