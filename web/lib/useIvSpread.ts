"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { IvSpreadData } from "./ivSpread";

// GET-only: radon-iv-spread.timer runs scripts/fetch_iv_spread.py every 5
// minutes in ET trading hours (IB serves the in-progress session bar), so the
// poll matches that cadence. There is no manual-scan POST. Types + pure
// helpers live in lib/ivSpread.ts; the route never transforms the payload.
const IV_SPREAD_SYNC_CONFIG = {
  endpoint: "/api/iv-spread",
  interval: 300_000,
  hasPost: false,
  extractTimestamp: (d: IvSpreadData) => d.scan_time,
};

export function useIvSpread(): UseSyncReturn<IvSpreadData> {
  return useSyncHook<IvSpreadData>(IV_SPREAD_SYNC_CONFIG, true);
}
