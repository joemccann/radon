/**
 * VIX P/C indicator - payload types + pure helpers for the 5-session
 * cumulative VIX put/call regime tab.
 *
 * Mirrors the GET /api/vix-pc contract: five-session sum of VIX put volume
 * divided by five-session sum of VIX call volume, ranked against stored
 * history. A low reading is a more cautious options tape; a high reading
 * means put volume dominates. Descriptive regime read only. No forward-
 * return claim.
 *
 * Constants below are display copy mirrored from the Python job. The UI
 * never recomputes a ratio. Spec: docs/indicators/vix-pc.md sections C, F, G.
 */

/* ─── Constants (display copy only) ──────────────────── */

/** Sessions in the cumulative window, inclusive of the current session. */
export const WINDOW = 5;

/** CAUTIOUS if percentile is strictly below this. */
export const BAND_PCTL_LOW = 10;

/** COMPLACENT if percentile is at or above this. */
export const BAND_PCTL_HIGH = 90;

/* ─── Payload types (spec E.4 / F.2) ─────────────────── */

export type VixPcStatus = "ok" | "stale_source" | "missing";
export type VixPcRegime = "CAUTIOUS" | "NORMAL" | "COMPLACENT";

export interface VixPcEntry {
  date: string;
  put_volume: number;
  call_volume: number;
  /** Null when call_volume <= 0. */
  daily_ratio: number | null;
  /** Null for the first WINDOW-1 rows and degenerate 5-session call sums. */
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
  stale?: boolean;
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

/** Three-decimal ratio: "0.319"; "---" when unavailable. */
export function formatRatio(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(3);
}

/** One-decimal percentile: "4.0"; "---" when unavailable. */
export function formatPctile(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(1);
}

/* ─── Regime bands (spec C.4, strict inequalities) ───── */

/**
 * percentile == 10 is NORMAL, == 90 is COMPLACENT.
 * Null and non-finite readings have no regime.
 */
export function vixPcRegime(p: number | null | undefined): VixPcRegime | null {
  if (p == null || !Number.isFinite(p)) return null;
  if (p < BAND_PCTL_LOW) return "CAUTIOUS";
  if (p < BAND_PCTL_HIGH) return "NORMAL";
  return "COMPLACENT";
}

/**
 * CAUTIOUS tones as a warning (call-heavy tape). COMPLACENT tones as a
 * dislocation (put-heavy tape). NORMAL stays muted.
 */
export function vixPcRegimeColor(regime: VixPcRegime | null | undefined): string {
  if (regime === "CAUTIOUS") return "var(--warning)";
  if (regime === "COMPLACENT") return "var(--dislocation)";
  return "var(--text-muted)";
}

/* ─── Chart rows ─────────────────────────────────────── */

export interface VixPcChartRow {
  date: string;
  /** Nulls survive as nulls: the cum5 line breaks, it never plots a zero. */
  cum5_ratio: number | null;
  daily_ratio: number | null;
}

export function buildVixPcChartRows(series: ReadonlyArray<VixPcEntry>): VixPcChartRow[] {
  return series.map((entry) => ({
    date: entry.date,
    cum5_ratio: entry.cum5_ratio != null && Number.isFinite(entry.cum5_ratio)
      ? entry.cum5_ratio
      : null,
    daily_ratio: entry.daily_ratio != null && Number.isFinite(entry.daily_ratio)
      ? entry.daily_ratio
      : null,
  }));
}
