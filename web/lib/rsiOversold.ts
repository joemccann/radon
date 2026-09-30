/**
 * RSI OVERSOLD — SPX percent of members with Wilder RSI(14) below 30.
 * Payload types + pure helpers for the RSI OVERSOLD regime tab.
 */

export interface RsiOversoldSource {
  constituents: string;
  constituents_count: number;
  member_close_fetches?: Record<string, number> | null;
}

export interface RsiOversoldPoint {
  date: string;
  pct_below_30: number;
  count_below_30: number;
  eligible: number;
  /** null when the ^GSPC overlay sweep missed the session. */
  spx_close: number | null;
}

export interface RsiOversoldCurrent extends RsiOversoldPoint {
  state: RsiOversoldState;
  cross_up: boolean;
  highest_since: string | null;
}

export interface RsiOversoldData {
  schema_version?: number;
  scan_time: string | null;
  data_date: string | null;
  source?: RsiOversoldSource | null;
  threshold: number | null;
  current: RsiOversoldCurrent | null;
  series: RsiOversoldPoint[];
  missing?: boolean;
}

export const MISSING_RSI_OVERSOLD: RsiOversoldData = Object.freeze({
  missing: true,
  scan_time: null,
  data_date: null,
  current: null,
  series: [] as RsiOversoldPoint[],
  threshold: null,
});

/** WallStreetCourier family level for this series. ASSUMPTION: chart unlabeled. */
export const RSI_OVERSOLD_THRESHOLD = 10;

export type RsiOversoldState = "OVERSOLD CLUSTER" | "NORMAL";

/** Strict: pct == 10 is NORMAL. */
export function rsiOversoldStateLabel(pct: number): RsiOversoldState {
  return pct > RSI_OVERSOLD_THRESHOLD ? "OVERSOLD CLUSTER" : "NORMAL";
}

export function rsiOversoldStateColor(state: RsiOversoldState): string {
  return state === "OVERSOLD CLUSTER" ? "var(--warning)" : "var(--text-muted)";
}

/** Previous session <= 10 and latest > 10. */
export function rsiOversoldCrossUp(series: RsiOversoldPoint[]): boolean {
  if (series.length < 2) return false;
  return series[series.length - 2].pct_below_30 <= RSI_OVERSOLD_THRESHOLD
    && series[series.length - 1].pct_below_30 > RSI_OVERSOLD_THRESHOLD;
}

export function formatRsiPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v.toFixed(1)}%`;
}

export function formatSpxClose(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
