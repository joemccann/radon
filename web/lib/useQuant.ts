"use client";

import { useMemo } from "react";
import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import type { QuantData } from "./quant";

/* ─── Hook ───────────────────────────────────────────────────── */

// GET-only: radon-quant-targets.timer writes the targets after the close and
// backtests are operator-run, so there is no manual-scan POST and an hourly
// poll is generous. Types + pure helpers live in lib/quant.ts.
const QUANT_POLL_MS = 60 * 60_000;

export function useQuant(strategy: string): UseSyncReturn<QuantData> {
  const config = useMemo(
    () => ({
      endpoint: `/api/quant/${encodeURIComponent(strategy)}`,
      interval: QUANT_POLL_MS,
      hasPost: false,
      extractTimestamp: (d: QuantData) => d.run?.run_at ?? d.targets?.recorded_at ?? null,
    }),
    [strategy],
  );
  return useSyncHook<QuantData>(config, true);
}
