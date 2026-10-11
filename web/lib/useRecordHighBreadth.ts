"use client";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { RecordHighBreadthData } from "./recordHighBreadth";
const CONFIG = {
  endpoint: "/api/record-high-breadth", interval: 3_600_000, hasPost: false,
  extractTimestamp: (data: RecordHighBreadthData) => data.scan_time || null,
  showBackgroundError: true,
};
export function useRecordHighBreadth(): UseSyncReturn<RecordHighBreadthData> {
  return useSyncHook<RecordHighBreadthData>(CONFIG, true);
}
