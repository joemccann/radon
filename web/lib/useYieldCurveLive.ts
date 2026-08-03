"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { YieldCurveLiveData } from "./yieldCurve";

/* ─── Hook ───────────────────────────────────────────────────── */

// Intraday 10Y-3M spread estimate (Yahoo ^TNX minus ^IRX). The route holds a
// 60s in-process cache, so a 5-minute poll while the tab is open keeps the
// cell current without hammering the upstream.
const YIELD_CURVE_LIVE_SYNC_CONFIG = {
  endpoint: "/api/yield-curve/live",
  interval: 5 * 60_000,
  hasPost: false,
  extractTimestamp: (d: YieldCurveLiveData) => d.asof || null,
};

export function useYieldCurveLive(): UseSyncReturn<YieldCurveLiveData> {
  return useSyncHook<YieldCurveLiveData>(YIELD_CURVE_LIVE_SYNC_CONFIG, true);
}
