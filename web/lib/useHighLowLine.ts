"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { HighLowLineData } from "./highLowLine";

const HIGH_LOW_LINE_SYNC_CONFIG = {
  endpoint: "/api/high-low-line",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (data: HighLowLineData) => data.scan_time,
};

export function useHighLowLine(): UseSyncReturn<HighLowLineData> {
  return useSyncHook<HighLowLineData>(HIGH_LOW_LINE_SYNC_CONFIG, true);
}
