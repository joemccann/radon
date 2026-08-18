"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const SYNC_INTERVAL_MS = 10 * 60 * 1000; // producer refreshes intraday; refetch while mounted

export type CatalystType = "earnings" | "fda" | "economic";

export interface CatalystRow {
  ticker: string | null;
  type: CatalystType;
  title: string;
  date: string;
  source: string;
  days_until: number;
  event_time?: string | null;
  forecast?: string | number | null;
  prev?: string | number | null;
  actual?: string | number | null;
  reported_period?: string | null;
  street_mean_est?: string | number | null;
  actual_eps?: string | number | null;
  expected_move_perc?: string | number | null;
}

export interface CatalystData {
  scan_time: string | null;
  count: number;
  catalysts: CatalystRow[];
  missing?: boolean;
}

interface UseCatalystsReturn {
  data: CatalystData | null;
  isLoading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useCatalysts(active: boolean = true): UseCatalystsReturn {
  const [data, setData] = useState<CatalystData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/catalysts", { cache: "no-store" });
      if (!res.ok) throw new Error("Failed to fetch catalysts");
      const json = (await res.json()) as CatalystData;
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

  useEffect(() => {
    if (!active) return;
    void load();
    intervalRef.current = setInterval(() => void load(), SYNC_INTERVAL_MS);
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [active, load]);

  return { data, isLoading, error, refresh };
}
