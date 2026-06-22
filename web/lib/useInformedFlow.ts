"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const SYNC_INTERVAL_MS = 10 * 60 * 1000; // 10 minutes — informed flow moves slowly

export type InformedFlowSide = "BUY" | "SELL" | "OTHER";

export interface CongressTrade {
  ticker: string;
  person: string;
  date: string;
  side: InformedFlowSide;
  amount?: string | null;
  days_ago: number;
}

export interface InsiderTrade {
  ticker: string;
  person: string;
  title?: string | null;
  date: string;
  side: InformedFlowSide;
  shares?: number | null;
  price?: number | null;
  days_ago: number;
}

export interface InstitutionalSummary {
  ticker: string;
  ownership_pct?: number | null;
  total_institutions?: number | null;
  report_date?: string | null;
}

export interface InformedFlowData {
  ticker: string;
  scan_time?: string | null;
  congress_trades: CongressTrade[];
  insider_trades: InsiderTrade[];
  institutional_summary: InstitutionalSummary | null;
  missing?: boolean;
}

interface UseInformedFlowReturn {
  data: InformedFlowData | null;
  isLoading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useInformedFlow(ticker: string | null, active: boolean = true): UseInformedFlowReturn {
  const [data, setData] = useState<InformedFlowData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    if (!ticker) return;
    try {
      const res = await fetch(`/api/informed-flow/${encodeURIComponent(ticker)}`, { cache: "no-store" });
      if (!res.ok) throw new Error("Failed to fetch informed flow");
      const json = (await res.json()) as InformedFlowData;
      setData(json);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setIsLoading(false);
    }
  }, [ticker]);

  const refresh = useCallback(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!active || !ticker) return;
    void load();
    intervalRef.current = setInterval(() => void load(), SYNC_INTERVAL_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [active, ticker, load]);

  return { data, isLoading, error, refresh };
}
