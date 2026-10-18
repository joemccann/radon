"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { VixPcData } from "./vixPc";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the 5-session VIX put/call series updates once per session
// (radon-vix-pc.timer runs scripts/fetch_vix_pc.py directly), so there is no
// manual-scan POST and an hourly poll is already generous. Types + pure
// helpers live in lib/vixPc.ts; the route never transforms the payload.
const VIX_PC_SYNC_CONFIG = {
  endpoint: "/api/vix-pc",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: VixPcData) => d.scan_time,
};

export function useVixPc(): UseSyncReturn<VixPcData> {
  return useSyncHook<VixPcData>(VIX_PC_SYNC_CONFIG, true);
}
