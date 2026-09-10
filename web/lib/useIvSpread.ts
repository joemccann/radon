"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { IvSpreadData } from "./ivSpread";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the NDX vs SPX 1M IV spread updates once per session
// (radon-iv-spread.timer runs scripts/fetch_iv_spread.py directly), so there
// is no manual-scan POST and an hourly poll is already generous. Types + pure
// helpers live in lib/ivSpread.ts; the route never transforms the payload.
const IV_SPREAD_SYNC_CONFIG = {
  endpoint: "/api/iv-spread",
  interval: 3_600_000,
  hasPost: false,
  extractTimestamp: (d: IvSpreadData) => d.scan_time,
};

export function useIvSpread(): UseSyncReturn<IvSpreadData> {
  return useSyncHook<IvSpreadData>(IV_SPREAD_SYNC_CONFIG, true);
}
