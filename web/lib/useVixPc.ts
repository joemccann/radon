"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { VixPcData } from "./vixPc";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: radon-vix-pc.timer re-upserts the UW VIX options-volume series
// on its own schedule, so there is no manual-scan POST and an hourly poll
// of a daily series is plenty. Types + pure helpers live in lib/vixPc.ts.
const VIX_PC_SYNC_CONFIG = {
  endpoint: "/api/vix-pc",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: VixPcData) => d.scan_time,
};

export function useVixPc(): UseSyncReturn<VixPcData> {
  return useSyncHook<VixPcData>(VIX_PC_SYNC_CONFIG, true);
}
