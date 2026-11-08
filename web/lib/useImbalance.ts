"use client";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { ImbalanceData } from "./imbalance";
const CONFIG = {
  endpoint: "/api/imbalance", interval: 3_600_000, hasPost: false,
  extractTimestamp: (data: ImbalanceData) => data.scan_time || null,
  showBackgroundError: true,
};
export function useImbalance(): UseSyncReturn<ImbalanceData> {
  return useSyncHook<ImbalanceData>(CONFIG, true);
}
