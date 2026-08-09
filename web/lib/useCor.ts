"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { CorData } from "./cor";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the series updates once per session (radon-cor timer runs
// the fetcher directly), so there is no manual-scan POST and an hourly poll
// is already generous. Types + pure helpers live in lib/cor.ts.
const COR_SYNC_CONFIG = {
  endpoint: "/api/cor",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: CorData) => d.scan_time,
};

export function useCor(): UseSyncReturn<CorData> {
  return useSyncHook<CorData>(COR_SYNC_CONFIG, true);
}
