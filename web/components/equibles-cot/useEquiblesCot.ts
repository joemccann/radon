"use client";

import { useSyncHook, type UseSyncReturn } from "@/lib/useSyncHook";
import type { CotPositioningData } from "./cotPositioning";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the CFTC publishes one report per week and the refresh timer runs
// the fetcher directly, so there is no manual-scan POST. Types + pure helpers
// live in cotPositioning.ts.
const COT_SYNC_CONFIG = {
  endpoint: "/api/equibles-cot-positioning",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: CotPositioningData) => d.scan_time || null,
};

export function useEquiblesCot(): UseSyncReturn<CotPositioningData> {
  return useSyncHook<CotPositioningData>(COT_SYNC_CONFIG, true);
}
