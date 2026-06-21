"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const SYNC_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

export type FlowSurpriseRow = {
  ticker: string;
  metric: string;
  status: string;
  flag: "EXCESS" | "DEFICIT" | "NORMAL";
  actual: number;
  expected_median: number;
  expected_p10?: number;
  expected_p90?: number;
  surprise_pit: number;
  surprise_ratio?: number | null;
  history_points?: number;
  as_of?: string;
};

export type FlowSurpriseData = {
  results: FlowSurpriseRow[];
  metric: string;
  scan_time: string;
  count_ok: number;
  skipped: number;
  missing?: boolean;
  cache_meta?: {
    last_refresh: string | null;
    age_seconds: number | null;
    is_stale: boolean;
    stale_threshold_seconds: number;
  };
};

type UseFlowSurpriseReturn = {
  data: FlowSurpriseData | null;
  isLoading: boolean;
  error: string | null;
  refresh: () => void;
};

export function useFlowSurprise(active: boolean = true): UseFlowSurpriseReturn {
  const [data, setData] = useState<FlowSurpriseData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/flow-surprise", { cache: "no-store" });
      if (!res.ok) throw new Error("Failed to fetch flow surprise data");
      const json = (await res.json()) as FlowSurpriseData;
      setData(json);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const refresh = useCallback(() => {
    void load();
  }, [load]);

  // Initial fetch
  useEffect(() => {
    if (!active) return;
    void load();
  }, [active, load]);

  // Auto-refresh interval (only when active)
  useEffect(() => {
    if (!active) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }

    intervalRef.current = setInterval(() => {
      void load();
    }, SYNC_INTERVAL_MS);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [active, load]);

  return { data, isLoading, error, refresh };
}
