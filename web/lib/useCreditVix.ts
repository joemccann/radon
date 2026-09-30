"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { CreditVixData } from "./creditVix";

const CREDIT_VIX_SYNC_CONFIG = {
  endpoint: "/api/credit-vix",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: CreditVixData) => d.scan_time || null,
};

export function useCreditVix(): UseSyncReturn<CreditVixData> {
  return useSyncHook<CreditVixData>(CREDIT_VIX_SYNC_CONFIG, true);
}
