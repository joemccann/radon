"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { IvRankData } from "./ivrank";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the SPY 1M IV series updates once per session
// (radon-ivrank.timer runs scripts/fetch_ivrank.py directly), so there is no
// manual-scan POST and an hourly poll is already generous. Types + pure
// helpers live in lib/ivrank.ts; the route never transforms the payload.
const IVRANK_SYNC_CONFIG = {
  endpoint: "/api/ivrank",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: IvRankData) => d.scan_time,
};

export function useIvRank(): UseSyncReturn<IvRankData> {
  return useSyncHook<IvRankData>(IVRANK_SYNC_CONFIG, true);
}
