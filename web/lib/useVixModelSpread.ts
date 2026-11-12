"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { VixModelSpreadData } from "./vixModelSpread";

const VIX_MODEL_SPREAD_SYNC_CONFIG = {
  endpoint: "/api/vix-model-spread",
  // radon-vix-model-spread.timer refreshes the provisional row every 5 minutes in ET trading hours.
  interval: 300_000,
  hasPost: false,
  extractTimestamp: (data: VixModelSpreadData) => data.scan_time,
};

export function useVixModelSpread(): UseSyncReturn<VixModelSpreadData> {
  return useSyncHook<VixModelSpreadData>(VIX_MODEL_SPREAD_SYNC_CONFIG, true);
}
