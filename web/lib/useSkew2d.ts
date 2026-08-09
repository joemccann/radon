"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { Skew2dData } from "./skew2d";
import { MarketState } from "./useMarketHours";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the series is daily (parent SKEW + 21:50 UTC 2d transform).
// Hourly poll when open; paused when closed. Types + pure helpers live in
// lib/skew2d.ts.
const SKEW2D_SYNC_CONFIG = {
  endpoint: "/api/skew2d",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: Skew2dData) => d.scan_time,
};

export function useSkew2d(marketState: MarketState | null = null): UseSyncReturn<Skew2dData> {
  const actualState = marketState ?? MarketState.OPEN;
  const interval = actualState === MarketState.OPEN ? 60 * 60_000 : 0;
  return useSyncHook<Skew2dData>({ ...SKEW2D_SYNC_CONFIG, interval }, true);
}
