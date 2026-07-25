"use client";

import { useSyncHook } from "./useSyncHook";
import type { BpiResponse } from "./bpi";

export interface UseBpiResult {
  data: BpiResponse | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only (useMarginDebt mold): the scan runs on the Mon-Fri 21:30 UTC
// radon-bpi timer and POST /bpi/scan is a FastAPI operator surface, so
// there is no client-side scan trigger and an hourly poll is generous
// for a once-per-session series. Types + guards live in lib/bpi.ts.
const BPI_SYNC_CONFIG = {
  endpoint: "/api/bpi",
  interval: 60 * 60_000,
  hasPost: false,
  extractTimestamp: (d: BpiResponse) => d.generated_at ?? null,
};

export function useBpi(): UseBpiResult {
  const { data, loading, error, syncNow } = useSyncHook<BpiResponse>(BPI_SYNC_CONFIG, true);
  return { data, loading, error, refresh: syncNow };
}
