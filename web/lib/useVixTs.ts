"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { VixTsData } from "./vixts";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: radon-vixts.timer re-upserts the whole Cboe VIX/VIX3M series on
// its own schedule, so there is no manual-scan POST and an hourly poll of a
// daily series is plenty. Types + pure helpers live in lib/vixts.ts.
const VIXTS_SYNC_CONFIG = {
  endpoint: "/api/vixts",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: VixTsData) => d.scan_time || null,
};

export function useVixTs(): UseSyncReturn<VixTsData> {
  return useSyncHook<VixTsData>(VIXTS_SYNC_CONFIG, true);
}
