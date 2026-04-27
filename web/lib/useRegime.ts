"use client";

import { useMemo } from "react";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import { isCriDataStale } from "./criStaleness";
import { MarketState } from "./useMarketHours";

export type CriHistoryEntry = {
  date: string;
  vix: number;
  vvix: number;
  spy: number;
  cor1m?: number;
  realized_vol?: number | null;
  spx_vs_ma_pct: number;
  vix_5d_roc: number;
};

export type CriData = {
  scan_time: string;
  market_open?: boolean;
  date: string;
  vix: number;
  vvix: number;
  spy: number;
  vix_5d_roc: number;
  vvix_vix_ratio: number | null;
  spx_100d_ma: number | null;
  spx_distance_pct: number;
  cor1m: number | null;
  cor1m_previous_close?: number | null;
  cor1m_5d_change: number | null;
  realized_vol: number | null;
  cri: {
    score: number;
    level: string;
    components: {
      vix: number;
      vvix: number;
      correlation: number;
      momentum: number;
    };
  };
  cta: {
    realized_vol: number;
    exposure_pct: number;
    forced_reduction_pct: number;
    est_selling_bn: number;
  };
  menthorq_cta: {
    date: string;
    source: string;
    spx: Record<string, unknown> | null;
    tables: Record<string, unknown[]>;
  } | null;
  crash_trigger: {
    triggered: boolean;
    conditions: {
      spx_below_100d_ma: boolean;
      realized_vol_gt_25: boolean;
      cor1m_gt_60: boolean;
    };
    values: Record<string, unknown>;
  };
  history: CriHistoryEntry[];
  nq_skew_history?: Array<{
    date: string;
    nq_skew: number;
    spx_position: number | null;
    nq_position: number | null;
    spx_skew?: number | null;
  }>;
  spx_skew_history?: Array<{
    date: string;
    spx_skew: number;
  }>;
  nasdaq_skew?: number | null;
  nq_skew?: number | null;
  spx_skew?: number | null;
  /** Last 21 SPY daily closes — used to compute intraday realized vol when live price is available */
  spy_closes?: number[];
};

function todayET(now = new Date()): string {
  return now.toLocaleDateString("sv", { timeZone: "America/New_York" });
}

export function needsCurrentEtSessionRetry(
  data: Pick<CriData, "date" | "scan_time" | "market_open"> | null | undefined,
  now = new Date(),
): boolean {
  if (!data?.scan_time) return true;
  const scanTimeMs = Date.parse(data.scan_time);
  if (!Number.isFinite(scanTimeMs)) return true;
  return isCriDataStale(
    { date: data.date, market_open: data.market_open },
    scanTimeMs,
    todayET(now),
  );
}

export const REGIME_STALE_RETRY_MS = 5000;

export const REGIME_SYNC_CONFIG = {
  endpoint: "/api/regime",
  interval: 60_000,
  hasPost: true,
  extractTimestamp: (d: CriData) => d.scan_time || null,
  shouldRetry: (d: CriData) => needsCurrentEtSessionRetry(d),
  retryIntervalMs: REGIME_STALE_RETRY_MS,
  retryMethod: "GET" as const,
};

type UseRegimeOptions = {
  endpoint?: string;
};

export function useRegime(marketState: boolean | MarketState | null = null, options: UseRegimeOptions = {}): UseSyncReturn<CriData> {
  // Backward compatibility: if called with boolean (old API), convert to MarketState
  let state: MarketState | null;
  if (typeof marketState === "boolean") {
    // true → OPEN, false → CLOSED
    state = marketState ? MarketState.OPEN : MarketState.CLOSED;
  } else {
    state = marketState;
  }

  // Default to OPEN if not specified (maintains backward compatibility)
  const actualState = state ?? MarketState.OPEN;

  // Adaptive interval based on market state
  // OPEN: 60s (1 minute) - full rate during regular hours
  // EXTENDED: 300s (5 minutes) - reduced rate during extended hours
  // CLOSED: 0 (paused) - no polling
  const adaptiveInterval: number =
    actualState === MarketState.OPEN ? 60_000 :
    actualState === MarketState.EXTENDED ? 300_000 :
    0;

  const endpoint = options.endpoint ?? REGIME_SYNC_CONFIG.endpoint;
  const stableConfig = useMemo(() => ({
    ...REGIME_SYNC_CONFIG,
    endpoint,
    interval: adaptiveInterval, // Override default interval
  }), [endpoint, adaptiveInterval]);

  return useSyncHook<CriData>(stableConfig, true);
}
