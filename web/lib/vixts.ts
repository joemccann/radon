/**
 * VIX TS — VIX / VIX3M term-structure ratio. Payload types, display
 * thresholds, and pure formatters for the VIX TS regime tab.
 *
 * Display only: the ratio and the regime are computed by the ingestion job
 * and carried in the payload. Nothing here recomputes them.
 *
 * Spec: docs/indicators/vixts.md sections C, D, G.
 */

export interface VixTsPoint {
  date: string;
  vix: number;
  vix3m: number;
  ratio: number;
  /** Left join for the chart overlay; absent for dates SPX does not cover. */
  spx: number | null;
}

export interface VixTsCurrent {
  date: string;
  vix: number;
  vix3m: number;
  ratio: number;
  regime: string;
  spx: number | null;
}

export interface VixTsStats {
  min: number;
  max: number;
  mean: number;
  median: number;
  days_backwardation: number;
  pct_backwardation: number;
  last_backwardation_date: string | null;
}

export interface VixTsData {
  scan_time: string | null;
  source_last_modified: Record<string, string> | null;
  data_date: string | null;
  count?: number;
  current: VixTsCurrent | null;
  stats: VixTsStats | null;
  series: VixTsPoint[];
  missing?: boolean;
}

// Contract: absent VIX TS data is HTTP 200 with missing:true, never a 4xx.
export const MISSING_VIXTS = Object.freeze({
  missing: true,
  scan_time: null,
  source_last_modified: null,
  data_date: null,
  current: null,
  stats: null,
  series: [] as VixTsPoint[],
});

export type VixTsRegime = "BACKWARDATION" | "FLAT" | "CONTANGO" | "STEEP CONTANGO";

/** Near-term vol bid over 3-month vol: the curve has flipped. */
export const BACKWARDATION_THRESHOLD = 1.0;
/** The approach to the flip; roughly 12 percent of sessions. */
export const FLAT_THRESHOLD = 0.95;
/** Below this is a rare complacency extreme, under 2 percent of sessions. */
export const STEEP_CONTANGO_THRESHOLD = 0.8;

export const SOURCE_FOOTNOTE =
  "Cboe VIX and VIX3M daily closes inner joined on session date since 2009-09-18, with the SPX close as a left-joined overlay. Statistics span the full history, not the visible range.";

/**
 * Half-open bands from the spec threshold table; each boundary belongs to the
 * band above. The guard clause pins the explicit CONTANGO default for
 * null/NaN — a bare chained-if would leak NaN past every comparison and land
 * it in a real band.
 */
export function vixTsRegimeLabel(ratio: number | null | undefined): VixTsRegime {
  if (ratio == null || !Number.isFinite(ratio)) return "CONTANGO";
  if (ratio >= BACKWARDATION_THRESHOLD) return "BACKWARDATION";
  if (ratio >= FLAT_THRESHOLD) return "FLAT";
  if (ratio >= STEEP_CONTANGO_THRESHOLD) return "CONTANGO";
  return "STEEP CONTANGO";
}

export function vixTsRegimeColor(regime: string): string {
  switch (regime) {
    case "BACKWARDATION": return "var(--negative)";
    case "FLAT": return "var(--warning)";
    case "STEEP CONTANGO": return "var(--positive)";
    default: return "var(--text-muted)";
  }
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
