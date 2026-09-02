"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { MaRatioData } from "./maRatio";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the ma-ratio refresh timer writes one row per session, so there
// is no manual-scan POST and an hourly poll is plenty for the series.
// Types + pure helpers live in lib/maRatio.ts.
const MA_RATIO_SYNC_CONFIG = {
  endpoint: "/api/ma-ratio",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: MaRatioData) => d.scan_time || null,
};

export function useMaRatio(): UseSyncReturn<MaRatioData> {
  return useSyncHook<MaRatioData>(MA_RATIO_SYNC_CONFIG, true);
}
