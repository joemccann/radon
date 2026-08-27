/**
 * VIX TS — the ratio of spot VIX (30-day implied volatility) to VIX3M
 * (3-month implied volatility), which is the slope of the volatility term
 * structure. Payload types + pure display helpers for the VIX TS regime tab.
 *
 * Below 1.00 the curve is in contango and near-term volatility is priced
 * below 3-month volatility. Above 1.00 it is in backwardation, which means
 * stress has moved into the near term. Descriptive regime read only: no
 * validation study was run, so no forward-return claim appears in the copy.
 *
 * The thresholds below are display copy mirrored from scripts/lib/vixts_math.py.
 * The UI never recomputes the ratio or the regime — the payload carries both.
 * Spec: docs/indicators/vixts.md.
 */

/* ─── Constants (display copy only) ──────────────────── */

/** Ratio at or above this: the front month is bid over the 3-month. */
export const BACKWARDATION_THRESHOLD = 1.0;
/** [0.95, 1.00): the curve is flattening toward the flip. */
export const FLAT_THRESHOLD = 0.95;
/** Below this: a rare complacency extreme, ~1.8% of the last three years. */
export const STEEP_CONTANGO_THRESHOLD = 0.8;

export const SOURCE_FOOTNOTE = "Source: CBOE";

/* ─── Payload types ──────────────────────────────────── */

export type VixTsRegime = "BACKWARDATION" | "FLAT" | "CONTANGO" | "STEEP CONTANGO";

/** Per-file Last-Modified stamps, lowercase keys, one per Cboe CSV. */
export interface VixTsSourceStamps {
  vix?: string | null;
  vix3m?: string | null;
  spx?: string | null;
}

export interface VixTsPoint {
  date: string;
  vix: number;
  vix3m: number;
  ratio: number;
  /** Left join on the SPX overlay: absent sessions carry null. */
  spx: number | null;
}

export interface VixTsCurrent {
  date: string;
  vix: number;
  vix3m: number;
  ratio: number;
  regime: VixTsRegime;
  spx: number | null;
}

export interface VixTsStats {
  min: number;
  max: number;
  mean: number;
  median: number;
  days_backwardation: number;
  pct_backwardation: number;
  /** Null when the series never crossed 1.00. */
  last_backwardation_date: string | null;
}

export interface VixTsData {
  scan_time: string | null;
  source_last_modified: VixTsSourceStamps | null;
  data_date: string | null;
  count?: number;
  current: VixTsCurrent | null;
  stats: VixTsStats | null;
  series: VixTsPoint[];
  missing?: boolean;
}

/** Contract: absent VIX TS data is HTTP 200 with missing:true, never a 4xx. */
export const MISSING_VIXTS: VixTsData = Object.freeze({
  missing: true,
  scan_time: null,
  source_last_modified: null,
  data_date: null,
  current: null,
  stats: null,
  series: [] as VixTsPoint[],
});

/* ─── Display helpers ────────────────────────────────── */

/**
 * Half-open bands from the spec threshold table; a boundary belongs to the
 * band above. The guard clause pins the explicit CONTANGO default for
 * null/NaN — a bare chained-if would leak NaN past every `>=` comparison and
 * land it in STEEP CONTANGO, a real band.
 */
export function vixTsRegimeLabel(ratio: number | null | undefined): VixTsRegime {
  if (ratio == null || !Number.isFinite(ratio)) return "CONTANGO";
  if (ratio >= BACKWARDATION_THRESHOLD) return "BACKWARDATION";
  if (ratio >= FLAT_THRESHOLD) return "FLAT";
  if (ratio >= STEEP_CONTANGO_THRESHOLD) return "CONTANGO";
  return "STEEP CONTANGO";
}

/**
 * BACKWARDATION tones as stress in the front of the curve; STEEP CONTANGO is
 * the calm extreme. CONTANGO is the ordinary state and stays muted.
 */
export function vixTsRegimeColor(regime: VixTsRegime | null | undefined): string {
  if (regime === "BACKWARDATION") return "var(--negative)";
  if (regime === "FLAT") return "var(--warning)";
  if (regime === "STEEP CONTANGO") return "var(--positive)";
  return "var(--text-muted)";
}

/** Term-structure ratio to four decimals: 0.848435 renders "0.8484". */
export function formatRatio(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(4);
}

/** Index level to two decimals: 15.21 renders "15.21". */
export function formatIndex(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}
