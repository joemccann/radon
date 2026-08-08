"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { SkewData } from "./skew";
import { MarketState } from "./useMarketHours";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the centralized writer owns UW calls. Browsers read the shared
// snapshot every minute during RTH without amplifying provider traffic.
const SKEW_SYNC_CONFIG = {
  endpoint: "/api/skew",
  interval: 60_000,
  hasPost: false,
  extractTimestamp: (d: SkewData) => d.scan_time,
};

export function useSkew(marketState: MarketState | null = null): UseSyncReturn<SkewData> {
  const actualState = marketState ?? MarketState.OPEN;
  const interval = actualState === MarketState.OPEN ? 60_000 : 0;
  return useSyncHook<SkewData>({ ...SKEW_SYNC_CONFIG, interval }, true);
}
