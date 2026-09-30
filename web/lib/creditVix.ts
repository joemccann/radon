/**
 * CREDIT/VIX — SHY minus HYG price gap (credit proxy) vs VIX.
 * Payload types + pure helpers for the /regime/credit-vix tab.
 */

export const WINDOW_SESSIONS = 252;
export const GAP_THRESHOLD = 0.5;

export type CreditVixState = "CREDIT WIDE" | "VIX WIDE" | "ALIGNED";

export interface CreditVixPoint {
  date: string;
  shy_close: number;
  hyg_close: number;
  vix_close: number;
  spread: number;
}

export interface CreditVixCurrent {
  date: string;
  shy_close: number;
  hyg_close: number;
  vix_close: number;
  spread: number;
  rank_spread: number;
  rank_vix: number;
  gap: number;
  state: CreditVixState;
  widest_since: string | null;
  window_sessions: number;
}

export interface CreditVixData {
  missing?: boolean;
  scan_time: string | null;
  source: string | null;
  count: number;
  current: CreditVixCurrent | null;
  series: CreditVixPoint[];
}

export type CreditVixPayload = CreditVixData;
export type CreditVixRow = CreditVixPoint;

export const MISSING_CREDIT_VIX: CreditVixData = Object.freeze({
  missing: true,
  scan_time: null,
  source: null,
  count: 0,
  current: null,
  series: [] as CreditVixPoint[],
});

export function formatSpread(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}

export function formatVix(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}

export function formatGap(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}

export function formatRank(pct: number | null | undefined): string {
  if (pct == null || !Number.isFinite(pct)) return "---";
  return `${Math.round(pct * 100)}%`;
}

export function stateLabel(state: CreditVixState | null | undefined): string {
  if (state === "CREDIT WIDE" || state === "VIX WIDE" || state === "ALIGNED") return state;
  return "---";
}

export type CreditVixTone = "positive" | "negative" | "muted";

export function stateTone(state: CreditVixState | null | undefined): CreditVixTone {
  if (state === "CREDIT WIDE") return "negative";
  if (state === "VIX WIDE") return "positive";
  return "muted";
}
