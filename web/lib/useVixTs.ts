"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { VixTsData } from "./vixts";

// GET-only: radon-vixts.timer refreshes the provisional current-session row
// every 5 minutes in ET trading hours, so the poll matches that cadence. There
// is no manual-scan POST. Types + pure helpers live in lib/vixts.ts.
const VIXTS_SYNC_CONFIG = {
  endpoint: "/api/vixts",
  interval: 300_000,
  hasPost: false,
  extractTimestamp: (d: VixTsData) => d.scan_time || null,
};

export function useVixTs(): UseSyncReturn<VixTsData> {
  return useSyncHook<VixTsData>(VIXTS_SYNC_CONFIG, true);
}
