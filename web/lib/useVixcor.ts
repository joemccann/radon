"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { VixcorData } from "./vixcor";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the joined VIX x COR3M series updates once per session
// (radon-vixcor.timer runs scripts/fetch_vixcor.py directly), so there is no
// manual-scan POST and an hourly poll is already generous. Types + pure
// helpers live in lib/vixcor.ts; the route never transforms the payload, so
// the job's "holding" / "stale_parent" statuses arrive here verbatim.
const VIXCOR_SYNC_CONFIG = {
  endpoint: "/api/vixcor",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: VixcorData) => d.scan_time,
};

export function useVixcor(): UseSyncReturn<VixcorData> {
  return useSyncHook<VixcorData>(VIXCOR_SYNC_CONFIG, true);
}
