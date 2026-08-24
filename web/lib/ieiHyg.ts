/**
 * IEI/HYG ratio with 52-week extremes — payload types + pure helpers for
 * the IEI/HYG regime tab. The ratio falls when high yield outperforms
 * Treasuries (risk-on); DXY is an overlay series only and never drives state.
 */

export const WINDOW_SESSIONS = 252;

// R-126: `unknown` is a window shorter than WINDOW_SESSIONS — a trailing
// slice makes the latest row the extreme almost every day while the series
// is still building, so there is no 52-week verdict to render yet.
export type IeiHygState = "new_low" | "new_high" | "neutral" | "unknown";

export interface IeiHygPoint {
  date: string;
  iei_close: number;
  hyg_close: number;
  dxy_close: number | null;
  ratio: number;
}

export interface IeiHygCurrent {
  date: string;
  iei_close: number;
  hyg_close: number;
  dxy_close: number | null;
  ratio: number;
  ratio_52w_low: number;
  low_date: string;
  ratio_52w_high: number;
  high_date: string;
  ratio_pct_rank: number | null;
  window_sessions: number;
  window_complete: boolean;
  state: IeiHygState;
}

export interface IeiHygData {
  missing?: boolean;
  scan_time: string | null;
  source: string | null;
  count: number;
  current: IeiHygCurrent | null;
  series: IeiHygPoint[];
}

export type IeiHygPayload = IeiHygData;
export type IeiHygRow = IeiHygPoint;

// Contract: absent IEI/HYG data is HTTP 200 with missing:true, never a 4xx.
export const MISSING_IEI_HYG: IeiHygData = Object.freeze({
  missing: true,
  scan_time: null,
  source: null,
  count: 0,
  current: null,
  series: [] as IeiHygPoint[],
});

export function formatRatio(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(4);
}

export function stateLabel(state: IeiHygState | null | undefined): string {
  if (state === "new_low") return "NEW 52W LOW";
  if (state === "new_high") return "NEW 52W HIGH";
  if (state === "neutral") return "NEUTRAL";
  if (state === "unknown") return "BUILDING WINDOW";
  return "---";
}

export type IeiHygTone = "positive" | "negative" | "muted";

export function stateTone(state: IeiHygState | null | undefined): IeiHygTone {
  if (state === "new_low") return "positive";
  if (state === "new_high") return "negative";
  return "muted";
}
