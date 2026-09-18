"use client";

import { useMemo } from "react";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { BounceSetupData } from "./bounceSetup";

const config = {
  endpoint: "/api/scanner/bounce",
  hasPost: false,
  extractTimestamp: (d: BounceSetupData) => d.scan_time || null,
  loadWhenInactive: false,
};

export function useBounceSetup(active: boolean): UseSyncReturn<BounceSetupData> {
  const stableConfig = useMemo(() => config, []);
  return useSyncHook<BounceSetupData>(stableConfig, active);
}
