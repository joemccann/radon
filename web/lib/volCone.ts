/**
 * VOL CONE indicator — payload types + pure helpers for the vol-cone regime tab.
 * Mirrors the GET /api/vol-cone contract: expiry-local ATM and 10% OTM wing
 * IVs versus that expiry's 90/10 cone. Spec: docs/indicators/vol-cone.md.
 */

export type VolConeRegime = "CHEAP_WINGS" | "CHEAP_ATM" | "RICH" | "NEUTRAL";

export interface VolConeSeriesPoint {
  date: string;
  spot: number;
  atm_iv: number | null;
  call_10_iv: number | null;
  put_10_iv: number | null;
}

export interface VolConeName {
  ticker: string;
  spot: number;
  expiry: string;
  dte: number;
  atm_iv: number | null;
  call_10_iv: number | null;
  put_10_iv: number | null;
  call_10_strike: number | null;
  put_10_strike: number | null;
  p10: number | null;
  p90: number | null;
  atm_percentile: number | null;
  call_10_percentile: number | null;
  put_10_percentile: number | null;
  wing_score: number | null;
  regime: VolConeRegime;
  series: VolConeSeriesPoint[];
}

export interface VolConeData {
  scan_time: string | null;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  source_as_of: string | null;
  count: number;
  hit_count: number;
  current: VolConeName | null;
  names: VolConeName[];
  hits: VolConeName[];
}

export interface VolConeChartRow {
  date: string;
  atm_iv: number | null;
  call_10_iv: number | null;
  put_10_iv: number | null;
}

/* ─── Formatting ─────────────────────────────────────── */

/** Decimal IV to one-decimal vol points: 0.3851 -> "38.5"; "---" null/non-finite. */
export function formatIvPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return (v * 100).toFixed(1);
}

/** Fraction -> one-decimal percent: 0.0556 -> "5.6%"; "---" null. */
export function formatPercentile(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${(v * 100).toFixed(1)}%`;
}

export function formatVolConeRegime(regime: VolConeRegime): string {
  return regime.replaceAll("_", " ");
}

/* ─── Derivations ────────────────────────────────────── */

export function isHit(regime: VolConeRegime): boolean {
  return regime === "CHEAP_WINGS" || regime === "CHEAP_ATM";
}

export function volConeRegimeColor(regime: VolConeRegime): string {
  switch (regime) {
    case "CHEAP_WINGS":
      return "var(--positive)";
    case "CHEAP_ATM":
      return "var(--warning)";
    case "RICH":
      return "var(--negative)";
    case "NEUTRAL":
      return "var(--text-muted)";
  }
}

/* ─── Chart rows ─────────────────────────────────────── */

export function buildVolConeChartRows(
  series: ReadonlyArray<VolConeSeriesPoint>,
): VolConeChartRow[] {
  return series.map((point) => ({
    date: point.date,
    atm_iv: point.atm_iv,
    call_10_iv: point.call_10_iv,
    put_10_iv: point.put_10_iv,
  }));
}
