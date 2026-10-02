"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { RsiOversoldData } from "./rsiOversold";

// GET-only: the rsi-oversold refresh timer writes one row per session, so
// there is no manual-scan POST and an hourly poll is plenty for the series.
const RSI_OVERSOLD_SYNC_CONFIG = {
  endpoint: "/api/rsi-oversold",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: RsiOversoldData) => d.scan_time || null,
};

export function useRsiOversold(): UseSyncReturn<RsiOversoldData> {
  return useSyncHook<RsiOversoldData>(RSI_OVERSOLD_SYNC_CONFIG, true);
}
