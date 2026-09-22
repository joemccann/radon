import { serializeLegsParam } from "./useChainUrlState";
import { formatExpiry } from "./optionsChainUtils";

/** docs/bounce-setup.md payload. */
export type BounceVerdict = "BOUNCE_SETUP" | "WATCH" | "STRETCHED";

export type BounceSeriesPoint = {
  date: string;
  spot_cum_pct: number;
  fs_iv_change: number;
  skew30: number | null;
};

export type BounceFlow = { signal: string; score: number } | null;

export type BounceSetupRow = {
  ticker: string;
  verdict: BounceVerdict;
  stretch_rank: number;
  stretch_pctl: number;
  rsi: number | null;
  pct_b: number | null;
  ret_z: number | null;
  ret_20d: number | null;
  contract: { symbol: string; expiry: string; strike: number } | null;
  vol: { runup: number | null; off_peak: number | null; slope: number | null; pass: boolean };
  skew: { ease: number | null; slope: number | null; pass: boolean };
  series: BounceSeriesPoint[];
  flow: BounceFlow;
  errors: string[];
};

export type BounceSetupData = {
  missing?: boolean;
  scan_time: string | null;
  as_of?: string;
  window?: number;
  universe?: string;
  coverage?: { tickers: number; ranked: number; excluded_short_history: number; stage2: number };
  bounce_count: number;
  results: BounceSetupRow[];
};

export const BOUNCE_VERDICT_LABELS: Record<BounceVerdict, string> = {
  BOUNCE_SETUP: "BOUNCE SETUP",
  WATCH: "WATCH",
  STRETCHED: "STRETCHED",
};

export function bounceVerdictLabel(verdict: string): string {
  return BOUNCE_VERDICT_LABELS[verdict as BounceVerdict] ?? verdict;
}

export function isFlowAccumulation(flow: BounceFlow | undefined): boolean {
  return flow?.signal === "ACCUMULATION";
}

/** Short leg about 5% above the long strike, at least 1 point. */
function shortStrike(strike: number): number {
  return strike + Math.max(1, Math.round(strike * 0.05));
}

/**
 * Defined-risk call spread prefill on the row's own contract expiry: buy the
 * call at the contract strike, sell the call about 5% above. Same URL shape as
 * the other scanner order links (`?deck=c`, `expiry`, `legs`, `src`).
 */
export function bounceOrderHref(row: BounceSetupRow): string | null {
  const ticker = row.ticker.trim().toUpperCase();
  const contract = row.contract;
  if (!ticker || !contract || !Number.isFinite(contract.strike)) return null;
  const legs = serializeLegsParam([
    { action: "BUY", quantity: 1, strike: contract.strike, right: "C" },
    { action: "SELL", quantity: 1, strike: shortStrike(contract.strike), right: "C" },
  ]);
  if (!legs) return null;
  const params = new URLSearchParams({
    deck: "c",
    expiry: formatExpiry(contract.expiry),
    strikes: "100",
    legs,
    src: "bounce",
  });
  return `/${encodeURIComponent(ticker)}?${params.toString()}`;
}
