"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { VolConeData } from "./volCone";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: radon-vol-cone.timer runs the fetcher daily, so there is no
// manual-scan POST and an hourly poll is already generous. Types + pure
// helpers live in lib/volCone.ts.
const VOL_CONE_SYNC_CONFIG = {
  endpoint: "/api/vol-cone",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: VolConeData) => d.scan_time || null,
};

export function useVolCone(): UseSyncReturn<VolConeData> {
  return useSyncHook<VolConeData>(VOL_CONE_SYNC_CONFIG, true);
}
