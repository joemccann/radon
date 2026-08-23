"use client";

import { useMemo } from "react";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { FlowAnalysisData } from "./types";

const config = {
  endpoint: "/api/flow-analysis",
  extractTimestamp: (d: FlowAnalysisData) => d.analysis_time || null,
  interval: 0,
};

export function useFlowAnalysis(active: boolean): UseSyncReturn<FlowAnalysisData> {
  const stableConfig = useMemo(() => config, []);
  return useSyncHook<FlowAnalysisData>(stableConfig, active);
}
