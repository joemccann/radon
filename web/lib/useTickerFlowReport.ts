"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isFlowReportStale } from "@/lib/flowReportStaleness";
import { flowReportErrorCopy } from "@/lib/flowReportError";

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
 *      - 202 scan_pending → state = { data: cache, status: "pending" }, then
 *        poll GET until cache_meta.last_refresh advances → "fresh"
 *      - error → preserve cached data if any, expose error
 */

/** `stale`: a POST came back 200 but degraded — cached data, not a scan.
 *  `pending`: the scan is still running on the server (R-464); any data
 *  held is the previous cache and GET is being polled for the landing. */
export type FlowReportStatus =
  | "idle" | "loading" | "scanning" | "pending" | "fresh" | "stale" | "error";

/** FastAPI bounds a scan at 300s; poll at 5s for as long as that could take. */
export const FLOW_REPORT_PENDING_POLL_MS = 5_000;
export const FLOW_REPORT_PENDING_POLL_LIMIT = 60;

export type UseTickerFlowReportOptions = {
  pendingPollMs?: number;
  pendingPollLimit?: number;
};

export type FlowReportData = {
  ticker: string;
  /** When true the cache is empty — no scan has run yet for this ticker. */
  missing?: boolean;
  /** Set by the route when it served cache instead of a completed scan. */
  is_stale?: boolean;
  /** 202 from the route: the scan outlived the request and is still running. */
  scan_pending?: boolean;
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

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}

function hasReport(payload: FlowReportData | null | undefined): payload is FlowReportData {
  return Boolean(payload && !payload.missing && payload.verdict);
}

export function useTickerFlowReport(
  ticker: string | null,
  options: UseTickerFlowReportOptions = {},
): UseTickerFlowReportReturn {
  const { pendingPollMs = FLOW_REPORT_PENDING_POLL_MS, pendingPollLimit = FLOW_REPORT_PENDING_POLL_LIMIT } = options;
  const [data, setData] = useState<FlowReportData | null>(null);
  const [status, setStatus] = useState<FlowReportStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const inflightRef = useRef<AbortController | null>(null);
  const triggerRef = useRef(0);

  // R-464: the route answers 202 when its 25s wait ends while FastAPI's
  // detached scan is still running. The landing is observed through GET:
  // `cache_meta.last_refresh` moving past what the 202 carried. Never a
  // second POST — that would only join the same in-flight task and wait
  // another 25s for the same 202.
  const awaitLanding = useCallback(
    async (sym: string, baseline: string | null, signal: AbortSignal) => {
      for (let poll = 0; poll < pendingPollLimit; poll += 1) {
        await sleep(pendingPollMs, signal);
        if (signal.aborted) return;
        let payload: FlowReportData | null = null;
        try {
          const res = await fetch(`/api/flow-analysis/${sym}`, { cache: "no-store", signal });
          if (res.ok) payload = (await res.json()) as FlowReportData;
        } catch {
          if (signal.aborted) return;
        }
        if (signal.aborted) return;
        const landed = payload?.cache_meta?.last_refresh ?? null;
        if (hasReport(payload) && landed && (baseline == null || landed > baseline)) {
          setData(payload);
          setStatus("fresh");
          return;
        }
      }
      setError(flowReportErrorCopy("Scan is still running; showing the last cached report."));
      setStatus("stale");
    },
    [pendingPollLimit, pendingPollMs],
  );

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
      if (res.status === 202 || payload?.scan_pending === true) {
        if (hasReport(payload)) setData(payload);
        setStatus("pending");
        await awaitLanding(sym, payload?.cache_meta?.last_refresh ?? null, signal);
        return;
      }
      setData(payload);
      // R-350: the route returns HTTP 200 with `{...cached, is_stale: true}`
      // plus an `X-Sync-Warning` header when its own 130s timeout fires, a
      // FastAPI 401 comes back, or a capacity shed persists — so accepting
      // any 200 as a completed scan rendered a scan that never ran as a FRESH
      // directional verdict with no banner. `isFlowReportStale` was consulted
      // only on the GET path.
      const warning = res.headers.get("X-Sync-Warning");
      if (payload?.is_stale === true || warning) {
        setError(flowReportErrorCopy(warning ?? "Scan did not complete; showing the last cached report."));
        setStatus("stale");
        return;
      }
      setStatus("fresh");
    } catch (err) {
      if (signal.aborted) return;
      const message = err instanceof Error ? err.message : "Flow scan failed";
      setError(flowReportErrorCopy(message));
      // Preserve any previously-cached data — caller decides how to display.
      setStatus("error");
    }
  }, [awaitLanding]);

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
        setError(flowReportErrorCopy(message));
        setData(null);
        setStatus("error");
      }
    },
    [triggerScan],
  );

  // R-349: this effect used to read `triggerRef.current` — a REF — in its dep
  // array behind an exhaustive-deps suppression, while `refresh` both mutated
  // that ref AND called `load` directly. One Refresh click therefore issued
  // TWO independent load cycles: `refresh`'s own `load(A)` set state
  // synchronously before its first await, and the resulting re-render made
  // the effect's dep compare 1 against the captured 0, aborting the in-flight
  // controller and firing `load` again. A client-side abort does not stop an
  // already-spawned FastAPI subprocess, so two 300s `flow_report.py` runs
  // occupied the general lane — the operator's own Refresh manufacturing the
  // capacity shed. `refresh` is now the SOLE caller for a re-scan and the
  // suppression is gone.
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
  }, [ticker, load]);

  const refresh = useCallback(() => {
    if (ticker) load(ticker);
  }, [ticker, load]);

  return { data, status, error, refresh };
}
