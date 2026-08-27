"use client";

import { useSyncHook, type UseSyncReturn } from "./useSyncHook";
import { MarketState } from "./useMarketHours";
import { isVcgDataStale } from "./vcgStaleness";

/* ─── VCG types (match vcg_scan.py JSON output) ─────────────── */

export type VcgSignal = {
  vcg: number | null;
  vcg_adj: number | null;      // was vcg_div — panic-adjusted z-score
  residual: number | null;
  beta1_vvix: number | null;
  beta2_vix: number | null;
  alpha: number | null;
  vix: number;
  vvix: number;
  credit_price: number;
  credit_5d_return_pct: number;
  ro: 0 | 1;
  edr: 0 | 1;                  // Early Divergence Risk
  tier: 1 | 2 | 3 | null;      // severity tier (1=critical, 2=high, 3=elevated)
  bounce: 0 | 1;               // counter-signal bounce detected
  vvix_severity: "extreme" | "elevated" | "moderate";
  sign_ok: boolean;
  sign_suppressed: boolean;
  pi_panic: number;
  regime: "PANIC" | "TRANSITION" | "DIVERGENCE";
  interpretation: "RISK_OFF" | "EDR" | "WATCH" | "BOUNCE" | "NORMAL" | "SUPPRESSED" | "PANIC";
  attribution: {
    vvix_pct: number;
    vix_pct: number;
    vvix_component: number;
    vix_component: number;
    model_implied: number;
  };
};

export type VcgHistoryEntry = {
  date: string;
  residual: number | null;
  vcg: number | null;
  vcg_adj: number | null;      // was vcg_div
  beta1: number | null;
  beta2: number | null;
  vix: number;
  vvix: number;
  credit: number;
};

export type VcgData = {
  scan_time: string;
  market_open: boolean;
  credit_proxy: string;
  /** True when both the Turso row and data/vcg.json were unreadable; `signal`
   *  is null alongside it. The route used to fabricate EMPTY_VCG, whose
   *  defaults read as an affirmative "no risk-off signal". R-228. */
  missing?: boolean;
  signal: VcgSignal | null;
  history: VcgHistoryEntry[];
};

/* ─── Staleness check ────────────────────────────────────────── */

// Delegate to the market-gated staleness lib so off-hours retries stop (the
// bare scanDate-vs-today check used to re-arm a 5s GET retry every weekend).
function needsVcgRetry(data: VcgData | null | undefined): boolean {
  if (!data) return true;
  return isVcgDataStale(data);
}

/* ─── Hook ───────────────────────────────────────────────────── */

const VCG_SYNC_CONFIG = {
  endpoint: "/api/vcg",
  interval: 60_000,
  hasPost: false,
  extractTimestamp: (d: VcgData) => d.scan_time || null,
  shouldRetry: (d: VcgData) => needsVcgRetry(d),
  retryIntervalMs: 5000,
  retryMethod: "GET" as const,
};

export function useVcg(marketState: MarketState | null = null): UseSyncReturn<VcgData> {
  let active: boolean;
  if (marketState === MarketState.OPEN || marketState === MarketState.EXTENDED) {
    active = true;
  } else if (marketState === MarketState.CLOSED) {
    active = false;
  } else {
    active = true; // unknown → poll
  }

  const config = {
    ...VCG_SYNC_CONFIG,
    interval: marketState === MarketState.EXTENDED ? 300_000 : 60_000,
  };

  return useSyncHook(config, active);
}
