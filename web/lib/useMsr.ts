"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { MsrData } from "./msr";

// GET-only: the series updates once per session (radon-msr timer runs the
// fetcher directly), so there is no manual-scan POST and an hourly poll is
// already generous. Types + pure helpers live in lib/msr.ts.
const MSR_SYNC_CONFIG = {
  endpoint: "/api/msr",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: MsrData) => d.scan_time,
};

export function useMsr(): UseSyncReturn<MsrData> {
  return useSyncHook<MsrData>(MSR_SYNC_CONFIG, true);
}
