"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { TrinData } from "./trin";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: radon-trin.timer samples IB every 5 minutes during RTH, so there
// is no manual-scan POST and a 5-minute poll matches the writer cadence.
// Types + pure helpers live in lib/trin.ts.
const TRIN_SYNC_CONFIG = {
  endpoint: "/api/trin",
  interval: 5 * 60_000,
  hasPost: false,
  extractTimestamp: (d: TrinData) => d.scan_time || null,
};

export function useTrin(): UseSyncReturn<TrinData> {
  return useSyncHook<TrinData>(TRIN_SYNC_CONFIG, true);
}
