"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { VixPcData } from "./vixPc";

// GET-only: radon-vix-pc.timer runs scripts/fetch_vix_pc.py every 5 minutes
// in ET trading hours (UW serves the in-progress session row), so the poll
// matches that cadence. There is no manual-scan POST. Types + pure helpers
// live in lib/vixPc.ts; the route never transforms the payload.
const VIX_PC_SYNC_CONFIG = {
  endpoint: "/api/vix-pc",
  interval: 300_000,
  hasPost: false,
  extractTimestamp: (d: VixPcData) => d.scan_time,
};

export function useVixPc(): UseSyncReturn<VixPcData> {
  return useSyncHook<VixPcData>(VIX_PC_SYNC_CONFIG, true);
}
