"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { HhLevData } from "./hhlev";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the hhlev timer re-upserts the quarterly Z.1 series on its own
// schedule, so there is no manual-scan POST and an hourly poll is plenty.
// Types + pure helpers live in lib/hhlev.ts.
const HHLEV_SYNC_CONFIG = {
  endpoint: "/api/hhlev",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: HhLevData) => d.scan_time || null,
};

export function useHhLev(): UseSyncReturn<HhLevData> {
  return useSyncHook<HhLevData>(HHLEV_SYNC_CONFIG, true);
}
