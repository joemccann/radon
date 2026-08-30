"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { DispersionData } from "./dispersion";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: radon-dispersion.timer re-emits the whole z-scored series on its
// own schedule, so there is no manual-scan POST and an hourly poll of a daily
// series is plenty. Types + pure helpers live in lib/dispersion.ts.
const DISPERSION_SYNC_CONFIG = {
  endpoint: "/api/dispersion",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: DispersionData) => d.scan_time || null,
};

export function useDispersion(): UseSyncReturn<DispersionData> {
  return useSyncHook<DispersionData>(DISPERSION_SYNC_CONFIG, true);
}
