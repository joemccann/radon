"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { IeiHygData } from "./ieiHyg";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the series updates once per calendar day (radon-iei-hyg timer
// runs the fetcher directly), so there is no manual-scan POST and an hourly
// poll is already generous. Types + pure helpers live in lib/ieiHyg.ts.
const IEI_HYG_SYNC_CONFIG = {
  endpoint: "/api/iei-hyg",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: IeiHygData) => d.scan_time || null,
};

export function useIeiHyg(): UseSyncReturn<IeiHygData> {
  return useSyncHook<IeiHygData>(IEI_HYG_SYNC_CONFIG, true);
}
