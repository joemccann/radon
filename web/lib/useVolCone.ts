"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { VolConeData } from "./volCone";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the scanner writes after the 16:45 ET close grace
// (radon-vol-cone timer). Hourly poll is already generous.
const VOL_CONE_SYNC_CONFIG = {
  endpoint: "/api/vol-cone",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: VolConeData) => d.scan_time,
};

export function useVolCone(): UseSyncReturn<VolConeData> {
  return useSyncHook<VolConeData>(VOL_CONE_SYNC_CONFIG, true);
}
