"use client";

import { useMemo } from "react";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { GarchConvergenceData } from "./types";

/**
 * useGarchConvergence — read-only hook for the GARCH cross-asset vol
 * repricing scan. Mirrors useLeap. Polls /api/garch-convergence (GET)
 * only; triggering a fresh scan goes through POST /api/garch-convergence
 * /scan separately (slow + cooldown-gated) and the caller then invokes
 * `syncNow()` to re-read the cache.
 */
const config = {
  endpoint: "/api/garch-convergence",
  hasPost: false,
  extractTimestamp: (d: GarchConvergenceData) => d.scan_time || null,
  loadWhenInactive: false,
};

export function useGarchConvergence(active: boolean): UseSyncReturn<GarchConvergenceData> {
  const stableConfig = useMemo(() => config, []);
  return useSyncHook<GarchConvergenceData>(stableConfig, active);
}
