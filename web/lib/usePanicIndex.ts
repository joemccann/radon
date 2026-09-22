"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { PanicIndexData } from "./panicIndex";

// GET-only: radon-panic-index.timer re-upserts the four-leg Cboe composite
// twice a day, so an hourly poll of a twice-daily series is plenty.
const PANIC_INDEX_SYNC_CONFIG = {
  endpoint: "/api/panic-index",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: PanicIndexData) => d.scan_time || null,
};

export function usePanicIndex(): UseSyncReturn<PanicIndexData> {
  return useSyncHook<PanicIndexData>(PANIC_INDEX_SYNC_CONFIG, true);
}
