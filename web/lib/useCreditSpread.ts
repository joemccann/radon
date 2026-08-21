"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { CreditSpreadData } from "./creditSpread";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the series updates once per calendar day (radon-credit-spread
// timer runs the fetcher directly), so there is no manual-scan POST and an
// hourly poll is already generous. Types + pure helpers live in lib/creditSpread.ts.
const CREDIT_SPREAD_SYNC_CONFIG = {
  endpoint: "/api/credit-spread",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: CreditSpreadData) => d.scan_time || null,
};

export function useCreditSpread(): UseSyncReturn<CreditSpreadData> {
  return useSyncHook<CreditSpreadData>(CREDIT_SPREAD_SYNC_CONFIG, true);
}
