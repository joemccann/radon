"use client";

import { useMemo } from "react";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { ThetaHarvesterData } from "./types";

const config = {
  endpoint: "/api/scanner/theta",
  hasPost: false,
  extractTimestamp: (d: ThetaHarvesterData) => d.scan_time || null,
};

export function useThetaHarvester(active: boolean): UseSyncReturn<ThetaHarvesterData> {
  const stableConfig = useMemo(() => config, []);
  return useSyncHook<ThetaHarvesterData>(stableConfig, active);
}
