"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { HyAdData } from "./hyad";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the hy-ad timer writes one row per bond-market session, so there
// is no manual-scan POST and an hourly poll is plenty for the series.
// Types + pure helpers live in lib/hyad.ts.
const HYAD_SYNC_CONFIG = {
  endpoint: "/api/hyad",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: HyAdData) => d.scan_time || null,
};

export function useHyAd(): UseSyncReturn<HyAdData> {
  return useSyncHook<HyAdData>(HYAD_SYNC_CONFIG, true);
}
