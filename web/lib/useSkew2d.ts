"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { Skew2dData } from "./skew2d";
import { MarketState } from "./useMarketHours";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the centralized writer owns the 2d transform. Series is daily, so
// poll hourly during RTH; paused when closed.
const SKEW2D_SYNC_CONFIG = {
  endpoint: "/api/skew2d",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: Skew2dData) => d.scan_time,
};

export function useSkew2d(marketState: MarketState | null = null): UseSyncReturn<Skew2dData> {
  const actualState = marketState ?? MarketState.OPEN;
  const interval = actualState === MarketState.OPEN ? 3_600_000 : 0;
  return useSyncHook<Skew2dData>({ ...SKEW2D_SYNC_CONFIG, interval }, true);
}
