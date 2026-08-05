"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { StraddleData } from "./straddle";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the series updates once per session (radon-straddle timer runs
// the fetcher directly), so there is no manual-scan POST and an hourly poll
// is already generous. Types + pure helpers live in lib/straddle.ts.
const STRADDLE_SYNC_CONFIG = {
  endpoint: "/api/straddle",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: StraddleData) => d.scan_time,
};

export function useStraddle(): UseSyncReturn<StraddleData> {
  return useSyncHook<StraddleData>(STRADDLE_SYNC_CONFIG, true);
}
