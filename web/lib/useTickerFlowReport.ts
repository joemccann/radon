"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isFlowReportStale } from "@/lib/flowReportStaleness";

/**
 * Cache-then-scan hook for a single-ticker flow report.
 *
 * Flow:
 *   1. GET /api/flow-analysis/{TICKER}
 *      - 200 + fresh         → state = { data, status: "fresh" }
 *      - 200 + stale         → state = { data, status: "scanning" }, then POST
 *      - 200 + missing:true  → state = { data: null, status: "scanning" }, then POST
 *      - 5xx                 → state = { error, status: "error" }
 *   2. POST /api/flow-analysis/{TICKER}
 *      - 200 → state = { data, status: "fresh" }
 *      - error → preserve cached data if any, expose error
 */

export type FlowReportStatus = "idle" | "loading" | "scanning" | "fresh" | "error";

export type FlowReportData = {
  ticker: string;
  /** When true the cache is empty — no scan has run yet for this ticker. */
  missing?: boolean;
  fetched_at?: string;
  lookback_days?: number;
  verdict?: { direction: "BULLISH" | "NEUTRAL" | "BEARISH"; confidence: number };
  analysis?: {
    signal?: string;
    score?: number;
    direction?: string;
    strength?: number;
    buy_ratio?: number | null;
    sustained_days?: number;
    num_prints?: number;
    options_conflict?: boolean;
  };
  dark_pool?: {
    aggregate?: {
      flow_direction?: string;
      flow_strength?: number;
      dp_buy_ratio?: number | null;
      total_volume?: number;
      total_premium?: number;
      buy_volume?: number;
      sell_volume?: number;
      num_prints?: number;
    };
    daily?: Array<{
      date: string;
      flow_direction?: string;
      flow_strength?: number;
      dp_buy_ratio?: number | null;
      num_prints?: number;
    }>;
  };
  options_flow?: {
    bias?: string;
    put_call_ratio?: number | null;
    /** @deprecated Compatibility for flow reports cached before the P/C migration. */
    call_put_ratio?: number | null;
    call_premium?: number;
    put_premium?: number;
    total_alerts?: number;
  };
  combined_signal?: string;
  market_status?: string;
  trading_days_checked?: string[];
  cache_meta?: {
    last_refresh?: string | null;
    age_seconds?: number | null;
    is_stale?: boolean;
  };
};

export type UseTickerFlowReportReturn = {
  data: FlowReportData | null;
  status: FlowReportStatus;
  error: string | null;
  refresh: () => void;
};

export function useTickerFlowReport(ticker: string | null): UseTickerFlowReportReturn {
  const [data, setData] = useState<FlowReportData | null>(null);
  const [status, setStatus] = useState<FlowReportStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const inflightRef = useRef<AbortController | null>(null);
  const triggerRef = useRef(0);

  const triggerScan = useCallback(async (sym: string, signal: AbortSignal) => {
    setStatus("scanning");
    setError(null);
    try {
      const res = await fetch(`/api/flow-analysis/${sym}`, {
        method: "POST",
        cache: "no-store",
        signal,
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(payload?.error ?? `Scan failed (${res.status})`);
      }
      const payload = (await res.json()) as FlowReportData;
      if (signal.aborted) return;
      setData(payload);
      setStatus("fresh");
    } catch (err) {
      if (signal.aborted) return;
      const message = err instanceof Error ? err.message : "Flow scan failed";
      setError(message);
      // Preserve any previously-cached data — caller decides how to display.
      setStatus("error");
    }
  }, []);

  const load = useCallback(
    async (sym: string) => {
      inflightRef.current?.abort();
      const ctrl = new AbortController();
      inflightRef.current = ctrl;
      const { signal } = ctrl;

      setData(null);
      setStatus("loading");
      setError(null);

      try {
        const res = await fetch(`/api/flow-analysis/${sym}`, {
          cache: "no-store",
          signal,
        });

        if (signal.aborted) return;

        if (res.ok) {
          const payload = (await res.json()) as FlowReportData;
          if (payload.ticker?.trim().toUpperCase() !== sym.trim().toUpperCase()) {
            throw new Error("Flow report response did not match ticker");
          }
          // Missing cache → don't pollute state with an empty payload, just scan.
          if (payload?.missing) {
            setData(null);
            await triggerScan(sym, signal);
            return;
          }
          setData(payload);
          if (isFlowReportStale(payload)) {
            await triggerScan(sym, signal);
          } else {
            setStatus("fresh");
          }
          return;
        }

        const payload = await res.json().catch(() => ({}));
        throw new Error(payload?.error ?? `Failed to load (${res.status})`);
      } catch (err) {
        if (signal.aborted) return;
        const message = err instanceof Error ? err.message : "Failed to load report";
        setError(message);
        setData(null);
        setStatus("error");
      }
    },
    [triggerScan],
  );

  useEffect(() => {
    if (!ticker) {
      setData(null);
      setStatus("idle");
      setError(null);
      return;
    }
    load(ticker);
    return () => {
      inflightRef.current?.abort();
    };
    // re-run when ticker or refresh trigger changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, triggerRef.current]);

  const refresh = useCallback(() => {
    triggerRef.current += 1;
    if (ticker) load(ticker);
  }, [ticker, load]);

  return { data, status, error, refresh };
}
