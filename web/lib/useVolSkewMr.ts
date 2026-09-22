"use client";

import { useMemo } from "react";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { VolSkewMrData } from "./types";

const config = {
  endpoint: "/api/scanner/vol-skew-mr",
  hasPost: false,
  extractTimestamp: (d: VolSkewMrData) => d.scan_time || null,
  loadWhenInactive: false,
};

export function useVolSkewMr(active: boolean): UseSyncReturn<VolSkewMrData> {
  const stableConfig = useMemo(() => config, []);
  return useSyncHook<VolSkewMrData>(stableConfig, active);
}
