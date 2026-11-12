"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { PanicIndexData } from "./panicIndex";

// GET-only: radon-panic-index.timer refreshes the provisional current-session
// row every 5 minutes in ET trading hours, so the poll matches that cadence.
const PANIC_INDEX_SYNC_CONFIG = {
  endpoint: "/api/panic-index",
  interval: 300_000,
  hasPost: false,
  extractTimestamp: (d: PanicIndexData) => d.scan_time || null,
};

export function usePanicIndex(): UseSyncReturn<PanicIndexData> {
  return useSyncHook<PanicIndexData>(PANIC_INDEX_SYNC_CONFIG, true);
}
