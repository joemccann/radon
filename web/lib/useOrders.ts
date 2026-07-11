"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { OrdersData } from "./types";

const POLL_INTERVAL_MS = 30_000;
const GET_FETCH_TIMEOUT_MS = 12_000;
const POST_FETCH_TIMEOUT_MS = 42_000;

type UseOrdersReturn = {
  data: OrdersData | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
  syncNow: () => void;
  updateData: (data: OrdersData) => void;
};

export function useOrders(active: boolean = true): UseOrdersReturn {
  const [data, setData] = useState<OrdersData | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const syncingRef = useRef(false);
  const readingRef = useRef(false);
  const warningSnapshotRef = useRef<string | null>(null);
  const dataGenerationRef = useRef(0);
  const pollCachedRef = useRef<() => Promise<void>>(async () => {});
  const initialLoadStartedRef = useRef(false);
  const previousActiveRef = useRef(active);
  const mountedRef = useRef(true);
  const activeRef = useRef(active);
  activeRef.current = active;

  const fetchOrders = useCallback(async () => {
    if (readingRef.current) return;
    readingRef.current = true;
    const generation = dataGenerationRef.current;
    try {
      const res = await fetch("/api/orders", {
        cache: "no-store",
        signal: AbortSignal.timeout(GET_FETCH_TIMEOUT_MS),
      });
      if (!res.ok) throw new Error(`Failed to fetch orders (${res.status})`);
      const json = (await res.json()) as OrdersData;
      if (!mountedRef.current || generation !== dataGenerationRef.current) return;
      setData(json);
      setLastSync(json.last_sync || null);
      if (
        warningSnapshotRef.current == null ||
        warningSnapshotRef.current !== json.last_sync
      ) {
        warningSnapshotRef.current = null;
        setError(null);
      }
    } catch (err) {
      if (mountedRef.current && generation === dataGenerationRef.current) {
        setError(err instanceof Error ? err.message : "Unknown error");
      }
    } finally {
      readingRef.current = false;
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  const scheduleNext = useCallback((delay: number) => {
    if (!mountedRef.current || !activeRef.current) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      void pollCachedRef.current();
    }, delay);
  }, []);

  const pollCached = useCallback(async () => {
    await fetchOrders();
    if (mountedRef.current && activeRef.current) scheduleNext(POLL_INTERVAL_MS);
  }, [fetchOrders, scheduleNext]);
  pollCachedRef.current = pollCached;

  const triggerSync = useCallback(async () => {
    if (syncingRef.current) return;
    syncingRef.current = true;
    const syncGeneration = ++dataGenerationRef.current;
    if (mountedRef.current) setSyncing(true);
    try {
      const res = await fetch("/api/orders", {
        method: "POST",
        cache: "no-store",
        signal: AbortSignal.timeout(POST_FETCH_TIMEOUT_MS),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error ?? "Sync failed");
      }
      const json = (await res.json()) as OrdersData;
      if (!mountedRef.current || syncGeneration !== dataGenerationRef.current) return;
      dataGenerationRef.current += 1;
      setData(json);
      setLastSync(json.last_sync || null);
      const warning = res.headers.get("X-Sync-Warning");
      if (warning) {
        warningSnapshotRef.current = json.last_sync || null;
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
    void triggerSync();
  }, [triggerSync]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (initialLoadStartedRef.current) return;
    initialLoadStartedRef.current = true;
    void fetchOrders();
  }, [fetchOrders]);

  useEffect(() => {
    const becameActive = active && !previousActiveRef.current;
    previousActiveRef.current = active;

    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (active) scheduleNext(becameActive ? 500 : POLL_INTERVAL_MS);

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [active, scheduleNext]);

  const updateData = useCallback((newData: OrdersData) => {
    dataGenerationRef.current += 1;
    setData(newData);
    setLastSync(newData.last_sync || null);
    warningSnapshotRef.current = null;
    setError(null);
  }, []);

  return { data, loading, syncing, error, lastSync, syncNow, updateData };
}
