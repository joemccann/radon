"use client";

import { useMemo } from "react";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { ScannerData } from "./types";

const config = {
  endpoint: "/api/scanner",
  extractTimestamp: (d: ScannerData) => d.scan_time || null,
  // Cold /scanner only GETs the active mode; inactive badges stay empty until selected (T7).
  loadWhenInactive: false,
};

export function useScanner(active: boolean): UseSyncReturn<ScannerData> {
  const stableConfig = useMemo(() => config, []);
  return useSyncHook<ScannerData>(stableConfig, active);
}
