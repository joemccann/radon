"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const DEFAULT_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes
type RetryMethod = "GET" | "POST";

type UseSyncConfig<T> = {
  endpoint: string;
  interval?: number;
  hasPost?: boolean; // default true; false = GET-only polling
  extractTimestamp?: (data: T) => string | null;
  shouldRetry?: (data: T) => boolean;
  retryIntervalMs?: number;
  retryMethod?: RetryMethod;
  showBackgroundError?: boolean;
};

export type UseSyncReturn<T> = {
  data: T | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
  syncNow: () => void;
};

export function useSyncHook<T>(config: UseSyncConfig<T>, active: boolean): UseSyncReturn<T> {
  const {
    endpoint,
    interval = DEFAULT_INTERVAL_MS,
    hasPost = true,
    extractTimestamp,
    shouldRetry,
    retryIntervalMs = 0,
    retryMethod = "POST",
    showBackgroundError = false,
  } = config;

  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const didInitialSync = useRef(false);
  const didInitialRead = useRef(false);
  const initialLoadKeyRef = useRef<string | null>(null);
  const requestRef = useRef<(method: RetryMethod, background?: boolean) => Promise<void>>(async () => {});

  const clearRetry = useCallback(() => {
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current);
      retryTimeoutRef.current = null;
    }
  }, []);

  const executeRequest = useCallback(async (method: RetryMethod, background = false) => {
    if (!background && method === "POST") {
      setSyncing(true);
    }
    try {
      const res = await fetch(endpoint, { method, cache: "no-store" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error ?? `Sync failed (${res.status})`);
      }
      const json = (await res.json()) as T;
      setData(json);
      setLastSync(extractTimestamp ? extractTimestamp(json) : new Date().toISOString());
      setError(null);

      clearRetry();
      if (active && shouldRetry?.(json) && retryIntervalMs > 0) {
        retryTimeoutRef.current = setTimeout(() => {
          void requestRef.current(retryMethod, true);
        }, retryIntervalMs);
      }
    } catch (err) {
      // Only show error if we don't already have valid cached data —
      // unless the caller explicitly wants the stale view marked as degraded.
      setData((prev) => {
        if (!prev || showBackgroundError) {
          setError(err instanceof Error ? err.message : "Sync failed");
        }
        return prev;
      });
    } finally {
      if (!background && method === "POST") {
        setSyncing(false);
      }
    }
  }, [active, clearRetry, endpoint, extractTimestamp, retryIntervalMs, retryMethod, shouldRetry, showBackgroundError]);

  requestRef.current = executeRequest;

  const triggerSync = useCallback(async () => {
    const method = hasPost ? "POST" : "GET";
    await executeRequest(method, false);
  }, [executeRequest, hasPost]);

  // Initial fetch — always read the cached file once when the hook mounts.
  // `active=false` should disable polling and background sync, not blank the page.
  useEffect(() => {
    if (initialLoadKeyRef.current === endpoint) return;
    initialLoadKeyRef.current = endpoint;

    const init = async () => {
      try {
        const res = await fetch(endpoint, { method: "GET", cache: "no-store" });
        if (!res.ok) throw new Error("Failed to fetch cached data");
        const json = (await res.json()) as T;
        setData(json);
        setLastSync(extractTimestamp ? extractTimestamp(json) : null);
        setError(null);
        setLoading(false);
        didInitialRead.current = true;

        clearRetry();
        if (active && shouldRetry?.(json) && retryIntervalMs > 0) {
          retryTimeoutRef.current = setTimeout(() => {
            void requestRef.current(retryMethod, true);
          }, retryIntervalMs);
        }

        // Auto-sync on first load when the hook is active.
        if (active && !didInitialSync.current) {
          didInitialSync.current = true;
          void triggerSync();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
        setLoading(false);
        didInitialRead.current = true;
        if (active && !didInitialSync.current) {
          didInitialSync.current = true;
          void triggerSync();
        }
      }
    };

    void init();
  }, [active, clearRetry, endpoint, triggerSync, extractTimestamp, retryIntervalMs, retryMethod, shouldRetry]);

  // If the hook mounted while inactive, issue the first sync when it later becomes active.
  useEffect(() => {
    if (!active || !didInitialRead.current || didInitialSync.current) return;
    didInitialSync.current = true;
    void triggerSync();
  }, [active, triggerSync]);

  // Auto-sync interval (only when active)
  useEffect(() => {
    if (!active || interval <= 0) {
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
  }, [active, clearRetry, interval, triggerSync]);

  const syncNow = useCallback(() => {
    void triggerSync();
  }, [triggerSync]);

  return { data, loading, syncing, error, lastSync, syncNow };
}
