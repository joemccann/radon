"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const SYNC_INTERVAL_MS = 10 * 60 * 1000; // 10 minutes — catalyst dates move slowly

export type CatalystType = "earnings" | "fda" | "economic";

export interface CatalystRow {
  ticker: string | null;
  type: CatalystType;
  title: string;
  date: string;
  source: string;
  days_until: number;
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
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [active, load]);

  return { data, isLoading, error, refresh };
}
