"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { IvRankData } from "./ivrank";

// GET-only: radon-ivrank.timer runs scripts/fetch_ivrank.py every 5 minutes
// in ET trading hours (IB serves the in-progress session's IV bar), so the
// poll matches that cadence. There is no manual-scan POST. Types + pure
// helpers live in lib/ivrank.ts; the route never transforms the payload.
const IVRANK_SYNC_CONFIG = {
  endpoint: "/api/ivrank",
  interval: 300_000,
  hasPost: false,
  extractTimestamp: (d: IvRankData) => d.scan_time,
};

export function useIvRank(): UseSyncReturn<IvRankData> {
  return useSyncHook<IvRankData>(IVRANK_SYNC_CONFIG, true);
}
