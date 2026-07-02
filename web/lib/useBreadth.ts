"use client";

import { useMemo } from "react";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import { MarketState } from "./useMarketHours";

/* ─── Breadth types (match the breadth-scan payload contract) ─── */

/**
 * Divergence rule (user-facing semantics — must match the backend collector):
 * over the last 20 sessions,
 *   spy_change_20d_pct > 1  AND cum_ad_change_20d < 0 -> "bearish"
 *   spy_change_20d_pct < -1 AND cum_ad_change_20d > 0 -> "bullish"
 *   otherwise -> "none"; null when inputs are missing.
 */
export type BreadthDivergence = "none" | "bearish" | "bullish" | null;

export type BreadthLatest = {
  session_date: string;
  /** Latest AD-NYSE value = NYSE advancers minus decliners. */
  net_ad: number | null;
  /** Prior session's daily net A/D close. */
  net_ad_prev: number | null;
  /** TICK-NYSE last value. */
  tick: number | null;
  /** Cumulative A/D line value at the latest session. */
  cum_ad: number | null;
  /** cum_ad now minus cum_ad 20 sessions ago. */
  cum_ad_change_20d: number | null;
  spy_change_20d_pct: number | null;
  /** Of the last 20 sessions, count with daily net_ad > 0. */
  sessions_positive_20: number | null;
  divergence: BreadthDivergence;
};

export type BreadthHistoryEntry = {
  date: string;
  net_ad: number;
  cum_ad: number;
  spy_close: number | null;
};

export type BreadthIntradayPoint = {
  time: string;
  net_ad: number;
};

export type BreadthData = {
  scan_time: string | null;
  market_open?: boolean;
  source?: "ib" | "cache";
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  latest: BreadthLatest | null;
  /** Oldest -> newest daily entries, up to ~1Y. */
  history: BreadthHistoryEntry[];
  /** Latest session 5-min bars; may be empty. */
  intraday: BreadthIntradayPoint[];
};

/* ─── Hook ───────────────────────────────────────────────────── */

const BREADTH_SYNC_CONFIG = {
  endpoint: "/api/breadth",
  interval: 60_000,
  hasPost: true,
  extractTimestamp: (d: BreadthData) => d.scan_time || null,
};

export function useBreadth(marketState: MarketState | null = null): UseSyncReturn<BreadthData> {
  const actualState = marketState ?? MarketState.OPEN;

  // Adaptive interval based on market state (mirrors useRegime):
  // OPEN: 60s / EXTENDED: 300s / CLOSED: 0 (paused).
  const adaptiveInterval: number =
    actualState === MarketState.OPEN ? 60_000 :
    actualState === MarketState.EXTENDED ? 300_000 :
    0;

  const config = useMemo(
    () => ({ ...BREADTH_SYNC_CONFIG, interval: adaptiveInterval }),
    [adaptiveInterval],
  );

  return useSyncHook<BreadthData>(config, true);
}
