"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { YieldCurveData } from "./yieldCurve";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the series updates once per business day (radon-yield-curve timer
// runs the fetcher directly), so there is no manual-scan POST and an hourly
// poll is already generous. Types + pure helpers live in lib/yieldCurve.ts.
const YIELD_CURVE_SYNC_CONFIG = {
  endpoint: "/api/yield-curve",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: YieldCurveData) => d.scan_time || null,
};

export function useYieldCurve(): UseSyncReturn<YieldCurveData> {
  return useSyncHook<YieldCurveData>(YIELD_CURVE_SYNC_CONFIG, true);
}
