"use client";

import { useMemo } from "react";
import { normalizePerformanceData } from "./performanceData";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { PerformanceData } from "./types";

const config = {
  endpoint: "/api/performance",
  interval: 15 * 60 * 1000,
  hasPost: false,
  extractTimestamp: (data: PerformanceData) => data.last_sync || data.as_of || null,
};

export function usePerformance(active: boolean): UseSyncReturn<PerformanceData> {
  const stableConfig = useMemo(() => config, []);
  const result = useSyncHook<PerformanceData>(stableConfig, active);
  const data = useMemo(() => normalizePerformanceData(result.data), [result.data]);
  return { ...result, data };
}
