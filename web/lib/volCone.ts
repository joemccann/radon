/**
 * VOL CONE indicator — payload types + pure helpers for the VOL CONE regime
 * tab. Mirrors GET /api/vol-cone: cheap 10% OTM wing IV vs that expiry's
 * 90/10 cone. Spec: docs/indicators/vol-cone.md.
 */

export type VolConeRegime = "CHEAP_WINGS" | "CHEAP_ATM" | "RICH" | "NEUTRAL";

export interface VolConeSeriesPoint {
  date: string;
  spot: number;
  /** Nulls are preserved for chart gaps. */
  atm_iv: number | null;
  call_10_iv: number | null;
  put_10_iv: number | null;
}

export interface VolConeName {
  ticker: string;
  spot: number;
  expiry: string;
  dte: number;
  atm_iv: number;
  call_10_iv: number;
  put_10_iv: number;
  call_10_strike: number;
  put_10_strike: number;
  p10: number;
  p90: number;
  atm_percentile: number;
  call_10_percentile: number;
  put_10_percentile: number;
  wing_score: number;
  regime: VolConeRegime;
  series: VolConeSeriesPoint[];
}

export interface VolConeData {
  scan_time: string | null;
  source_as_of: string | null;
  count: number;
  hit_count: number;
  current: VolConeName | null;
  names: VolConeName[];
  hits: VolConeName[];
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
}

/* ─── Formatting ─────────────────────────────────────── */

/** Decimal IV to one-decimal vol points: 0.38513 -> "38.5"; "---" null. */
export function formatIvPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return (v * 100).toFixed(1);
}

/** Fraction -> one-decimal percent: 0.05555 -> "5.6%"; "---" null. */
export function formatPercentile(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${(v * 100).toFixed(1)}%`;
}

/* ─── Derivations ────────────────────────────────────── */

/** Cheap-wing or cheap-ATM names are hits; NEUTRAL / RICH are not. */
export function isHit(regime: VolConeRegime): boolean {
  return regime === "CHEAP_WINGS" || regime === "CHEAP_ATM";
}

export function volConeRegimeColor(regime: VolConeRegime): string {
  if (regime === "CHEAP_WINGS") return "var(--positive)";
  if (regime === "CHEAP_ATM") return "var(--warning)";
  if (regime === "RICH") return "var(--negative)";
  return "var(--text-muted)";
}

/* ─── Chart rows ─────────────────────────────────────── */

export interface VolConeChartRow {
  date: string;
  /** Null ATM prints are kept so the chart can gap them. */
  atm_iv: number | null;
  call_10_iv: number | null;
  put_10_iv: number | null;
}

export function buildVolConeChartRows(
  series: ReadonlyArray<VolConeSeriesPoint>,
): VolConeChartRow[] {
  return series.map((entry) => ({
    date: entry.date,
    atm_iv: entry.atm_iv,
    call_10_iv: entry.call_10_iv,
    put_10_iv: entry.put_10_iv,
  }));
}
