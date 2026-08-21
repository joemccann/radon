"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { CreditSpreadData } from "./creditSpread";

const CREDIT_SPREAD_SYNC_CONFIG = {
  endpoint: "/api/credit-spread",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: CreditSpreadData) => d.scan_time || null,
};

export function useCreditSpread(): UseSyncReturn<CreditSpreadData> {
  return useSyncHook<CreditSpreadData>(CREDIT_SPREAD_SYNC_CONFIG, true);
}
