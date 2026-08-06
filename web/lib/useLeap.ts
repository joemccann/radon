"use client";

import { useMemo } from "react";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { LeapData } from "./types";

/**
 * useLeap — read-only hook for the LEAP IV-mispricing scan. Mirrors
 * useScanner. Polls /api/leap (GET) only; triggering a fresh scan goes
 * through POST /api/leap/scan separately (slow + cooldown-gated) and the
 * caller then invokes `syncNow()` to re-read the cache.
 */
const config = {
  endpoint: "/api/leap",
  hasPost: false,
  extractTimestamp: (d: LeapData) => d.scan_time || null,
  loadWhenInactive: false,
};

export function useLeap(active: boolean): UseSyncReturn<LeapData> {
  const stableConfig = useMemo(() => config, []);
  return useSyncHook<LeapData>(stableConfig, active);
}
