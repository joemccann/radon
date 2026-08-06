"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { SkewData } from "./skew";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the series updates once per session (radon-skew timer runs the
// fetcher directly), so there is no manual-scan POST and an hourly poll is
// already generous. Types + pure helpers live in lib/skew.ts.
const SKEW_SYNC_CONFIG = {
  endpoint: "/api/skew",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: SkewData) => d.scan_time,
};

export function useSkew(): UseSyncReturn<SkewData> {
  return useSyncHook<SkewData>(SKEW_SYNC_CONFIG, true);
}
