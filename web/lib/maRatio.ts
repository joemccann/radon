/**
 * MA RATIO — SPX percent of members above their 50-day SMA over percent
 * above their 200-day SMA (the StockCharts $SPXA50R:$SPXA200R construction,
 * computed from constituent closes). Payload types + pure helpers for the
 * MA RATIO regime tab.
 */

export interface MaRatioSource {
  constituents: string;
  constituents_count: number;
  member_close_fetches?: Record<string, number> | null;
}

export interface MaRatioPoint {
  date: string;
  pct_above_50: number;
  pct_above_200: number;
  /** null on a full 200-day washout (zero-denominator guard). */
  ratio: number | null;
  /** null when the ^GSPC overlay sweep missed the session. */
  spx_close: number | null;
}

export interface MaRatioCurrent extends MaRatioPoint {
  count_above_50: number;
  count_above_200: number;
  eligible_50: number;
  eligible_200: number;
}

export interface MaRatioZone {
  low: number;
  high: number;
}

export interface MaRatioData {
  schema_version?: number;
  scan_time: string | null;
  data_date: string | null;
  source?: MaRatioSource | null;
  zone: MaRatioZone | null;
  current: MaRatioCurrent | null;
  series: MaRatioPoint[];
  missing?: boolean;
}

// Contract: absent MA RATIO data is HTTP 200 with missing:true, never a 4xx.
export const MISSING_MA_RATIO: MaRatioData = Object.freeze({
  missing: true,
  scan_time: null,
  data_date: null,
  current: null,
  series: [] as MaRatioPoint[],
  zone: null,
});

/** Signal zone confirmed against the 1 Sep 2026 StockCharts reference lines. */
export const MA_RATIO_ZONE: MaRatioZone = Object.freeze({ low: 0.25, high: 0.5 });

export type MaRatioState = "WASHED OUT" | "SIGNAL ZONE" | "50D LAGGING" | "50D LEADING";

/**
 * Ratio bands (pinned at the boundaries in tests): below the zone is a full
 * washout, the 0.25-0.5 zone is inclusive at BOTH edges, above it the 50d
 * cohort lags the 200d cohort until parity at 1.0.
 */
export function maRatioStateLabel(ratio: number): MaRatioState {
  if (ratio < MA_RATIO_ZONE.low) return "WASHED OUT";
  if (ratio <= MA_RATIO_ZONE.high) return "SIGNAL ZONE";
  if (ratio < 1.0) return "50D LAGGING";
  return "50D LEADING";
}

export function maRatioStateColor(state: MaRatioState): string {
  switch (state) {
    case "WASHED OUT": return "var(--negative)";
    case "SIGNAL ZONE": return "var(--warning)";
    case "50D LAGGING": return "var(--text-muted)";
    case "50D LEADING": return "var(--positive)";
  }
}

export function isInSignalZone(ratio: number | null | undefined): boolean {
  if (ratio == null || !Number.isFinite(ratio)) return false;
  return ratio >= MA_RATIO_ZONE.low && ratio <= MA_RATIO_ZONE.high;
}

/**
 * Buy-style signal: the previous session's ratio sat inside the 0.25-0.5
 * zone and the latest ratio turned STRICTLY up from it. A reading inside the
 * zone that has not turned up is a wash-out condition, not yet a signal.
 */
export function maRatioZoneTurnUp(series: MaRatioPoint[]): boolean {
  if (series.length < 2) return false;
  const prev = series[series.length - 2].ratio;
  const latest = series[series.length - 1].ratio;
  if (prev == null || latest == null) return false;
  return isInSignalZone(prev) && latest > prev;
}

export function formatMaRatio(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}

export function formatMaPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v.toFixed(1)}%`;
}

export function formatSpxClose(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
