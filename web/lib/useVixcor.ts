"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { VixcorData } from "./vixcor";

// GET-only: radon-vixcor.timer runs scripts/fetch_vixcor.py every 5 minutes in
// ET trading hours (provisional vixcor:live session) and once after the close
// (official series), so there is no manual-scan POST and the poll matches the
// intraday tick. Types + pure helpers live in lib/vixcor.ts; the route only
// appends the provisional row, so the job's "holding" / "stale_parent"
// statuses arrive here verbatim.
const VIXCOR_SYNC_CONFIG = {
  endpoint: "/api/vixcor",
  interval: 300_000,
  hasPost: false,
  extractTimestamp: (d: VixcorData) => d.scan_time,
};

export function useVixcor(): UseSyncReturn<VixcorData> {
  return useSyncHook<VixcorData>(VIXCOR_SYNC_CONFIG, true);
}
