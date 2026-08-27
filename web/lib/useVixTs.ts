"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { VixTsData } from "./vixts";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the radon-vixts timer re-upserts the daily Cboe series on its own
// schedule, so there is no manual-scan POST and an hourly poll is plenty.
// Types + pure helpers live in lib/vixts.ts.
const VIXTS_SYNC_CONFIG = {
  endpoint: "/api/vixts",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: VixTsData) => d.scan_time || null,
};

export function useVixTs(): UseSyncReturn<VixTsData> {
  return useSyncHook<VixTsData>(VIXTS_SYNC_CONFIG, true);
}
