"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { MarginDebtData } from "./marginDebt";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: the series updates monthly (radon-margin-debt-refresh timer runs
// the fetcher directly), so there is no manual-scan POST and an hourly poll
// is already generous. Types + pure helpers live in lib/marginDebt.ts.
const MARGIN_DEBT_SYNC_CONFIG = {
  endpoint: "/api/margin-debt",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: MarginDebtData) => d.scan_time || null,
};

export function useMarginDebt(): UseSyncReturn<MarginDebtData> {
  return useSyncHook<MarginDebtData>(MARGIN_DEBT_SYNC_CONFIG, true);
}
