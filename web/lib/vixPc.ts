/**
 * VIX P/C indicator - payload types + pure display helpers for the 5-session
 * cumulative VIX put/call ratio regime tab.
 *
 * The UI never recomputes a ratio. Display constants mirror the Python job.
 * Spec: docs/indicators/vix-pc.md sections C.1, C.4, G.
 */

/* ─── Constants (display copy only) ──────────────────── */

/** Sessions in the cumulative window, inclusive of the current session. */
export const WINDOW = 5;

/** CAUTIOUS if percentile is strictly below this. */
export const BAND_PCTL_LOW = 10;

/** COMPLACENT if percentile is at or above this. */
export const BAND_PCTL_HIGH = 90;

/* ─── Payload types (spec E.4) ───────────────────────── */

export type VixPcRegime = "CAUTIOUS" | "NORMAL" | "COMPLACENT";
export type VixPcStatus = "ok" | "stale_source" | "missing";

export interface VixPcEntry {
  date: string;
  put_volume: number;
  call_volume: number;
  daily_ratio: number | null;
  cum5_ratio: number | null;
}

export interface VixPcCurrent {
  date: string;
  put_volume: number;
  call_volume: number;
  daily_ratio: number | null;
  cum5_ratio: number | null;
  percentile: number | null;
  regime: VixPcRegime | null;
  series_min: number | null;
  series_max: number | null;
  series_p10: number | null;
  series_p90: number | null;
  series_min_date: string | null;
  is_series_low: boolean;
}

export interface VixPcStats {
  min: number | null;
  p10: number | null;
  p25: number | null;
  median: number | null;
  p75: number | null;
  p90: number | null;
  max: number | null;
  mean: number | null;
}

export interface VixPcData {
  scan_time: string | null;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  status: VixPcStatus;
  source?: "uw" | null;
  as_of: string | null;
  data_date: string | null;
  expected_session?: string | null;
  market_status?: string | null;
  window?: number;
  count: number;
  cum5_count?: number;
  current: VixPcCurrent | null;
  stats: VixPcStats | null;
  series: VixPcEntry[];
}

/* ─── Formatting ─────────────────────────────────────── */

/** Three-decimal ratio: 0.3188487844029556 -> "0.319"; "---" unavailable. */
export function formatRatio(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(3);
}

/** One-decimal percentile: 4.032258064516129 -> "4.0"; "---" unavailable. */
export function formatPctile(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(1);
}

/* ─── Regime bands (spec C.4, strict inequalities) ───── */

/**
 * percentile == 10 is NORMAL, == 90 is COMPLACENT.
 * Null and non-finite readings have no regime.
 */
export function vixPcRegime(v: number | null | undefined): VixPcRegime | null {
  if (v == null || !Number.isFinite(v)) return null;
  if (v < BAND_PCTL_LOW) return "CAUTIOUS";
  if (v < BAND_PCTL_HIGH) return "NORMAL";
  return "COMPLACENT";
}

/**
 * COMPLACENT tones as a dislocation, not a fault: put-heavy VIX tape is a
 * structural state of the options tape, and --negative would over-claim.
 */
export function vixPcRegimeColor(regime: VixPcRegime | null | undefined): string {
  if (regime === "CAUTIOUS") return "var(--warning)";
  if (regime === "COMPLACENT") return "var(--dislocation)";
  return "var(--text-muted)";
}

/* ─── Chart rows ─────────────────────────────────────── */

export interface VixPcChartRow {
  date: string;
  /** Nulls survive as nulls: the line breaks, it never plots a zero. */
  cum5_ratio: number | null;
}

export function buildVixPcChartRows(series: ReadonlyArray<VixPcEntry>): VixPcChartRow[] {
  return series.map((entry) => ({
    date: entry.date,
    cum5_ratio: entry.cum5_ratio != null && Number.isFinite(entry.cum5_ratio)
      ? entry.cum5_ratio
      : null,
  }));
}
