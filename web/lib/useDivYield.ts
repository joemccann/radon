"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { DivYieldData } from "./divyield";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the div-yield refresh timer writes one row per session, so there
// is no manual-scan POST and an hourly poll is plenty for the series.
// Types + pure helpers live in lib/divyield.ts.
const DIVYIELD_SYNC_CONFIG = {
  endpoint: "/api/divyield",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: DivYieldData) => d.scan_time || null,
};

export function useDivYield(): UseSyncReturn<DivYieldData> {
  return useSyncHook<DivYieldData>(DIVYIELD_SYNC_CONFIG, true);
}
