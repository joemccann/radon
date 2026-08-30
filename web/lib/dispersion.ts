/**
 * DISPERSION — VIX vs single-stock vs cross-sector dispersion, each a rolling
 * 60-session mean z-scored over the full sample since 2017. Payload types +
 * pure display helpers for the DISPERSION regime tab.
 *
 * Single-stock volatility is the 95th minus 5th percentile of daily returns
 * across the S&P 500 constituent seed; cross-sector volatility is the same
 * spread across the 11 Select Sector SPDRs. When those two lines run above
 * one sigma while the VIX sits below zero, volatility is rising below the
 * surface: index hedges are cheap while idiosyncratic risk is extreme.
 *
 * The constants below are display copy mirrored from
 * scripts/lib/dispersion_math.py. The UI never recomputes a spread, a z-score
 * or the regime — the payload carries all of them.
 * Spec: docs/indicators/dispersion.md.
 */

/* ─── Constants (display copy only) ──────────────────── */

/** z at or above this on the VIX is BROAD STRESS; on stock/sector it is BELOW THE SURFACE. */
export const STRESS_Z = 1.0;
/** All three at or below this: COMPRESSED. */
export const COMPRESSED_Z = -1.0;
/** Rolling window, in sessions, behind every plotted metric. */
export const WINDOW = 60;
/** Start of the z-score base sample. */
export const ZSCORE_BASE_START = "2017-01-01";

export const SOURCE_FOOTNOTE = "Source: Interactive Brokers daily bars (Yahoo fallback)";

/* ─── Payload types ──────────────────────────────────── */

export type DispersionRegime = "BROAD STRESS" | "BELOW THE SURFACE" | "COMPRESSED" | "NORMAL";

export type DispersionSourceKind = "ib" | "yahoo" | "mixed" | "stored" | "none";

export interface DispersionSource {
  prices: DispersionSourceKind;
  vix: DispersionSourceKind;
}

export interface DispersionUniverse {
  index: string;
  n_constituents: number;
  sectors: string[];
}

export interface DispersionFetch {
  ib_ok: number;
  yahoo_ok: number;
  failed: number;
  failed_symbols: string[];
}

export interface DispersionPoint {
  date: string;
  z_vix: number;
  z_stock: number;
  z_sector: number;
  vix: number;
  stock_spread: number;
  sector_spread: number;
}

export interface DispersionCurrent extends DispersionPoint {
  m60_vix: number;
  m60_stock: number;
  m60_sector: number;
  n_stocks: number;
  n_sectors: number;
  regime: DispersionRegime;
  /** max(z_stock, z_sector) minus z_vix: the volatility hiding below the surface. */
  surface_gap: number;
}

export interface DispersionSeriesStats {
  mean_60d: number;
  stdev_60d: number;
  z_min: number;
  z_max: number;
}

export interface DispersionStats {
  base: { start: string; end: string; n: number };
  vix: DispersionSeriesStats;
  stock: DispersionSeriesStats;
  sector: DispersionSeriesStats;
  days_below_surface: number;
  /** Null when the series never printed BELOW THE SURFACE. */
  last_below_surface_date: string | null;
}

export interface DispersionData {
  scan_time: string | null;
  status: "ok" | "stale_source" | null;
  source: DispersionSource | null;
  data_date: string | null;
  universe: DispersionUniverse | null;
  fetch: DispersionFetch | null;
  count: number;
  current: DispersionCurrent | null;
  stats: DispersionStats | null;
  series: DispersionPoint[];
  missing?: boolean;
  stale?: boolean;
}

/** Contract: absent DISPERSION data is HTTP 200 with missing:true, never a 4xx. */
export const MISSING_DISPERSION: DispersionData = Object.freeze({
  missing: true,
  scan_time: null,
  status: null,
  source: null,
  data_date: null,
  universe: null,
  fetch: null,
  count: 0,
  current: null,
  stats: null,
  series: [] as DispersionPoint[],
});

/* ─── Display helpers ────────────────────────────────── */

/**
 * BROAD STRESS tones as index-level stress; BELOW THE SURFACE is the warning
 * the tab exists to show; COMPRESSED is the calm extreme. NORMAL stays muted.
 */
export function regimeTone(regime: DispersionRegime | null | undefined): string {
  if (regime === "BROAD STRESS") return "var(--negative)";
  if (regime === "BELOW THE SURFACE") return "var(--warning)";
  if (regime === "COMPRESSED") return "var(--positive)";
  return "var(--text-muted)";
}

/** Signed z-score to two decimals: 2.38 renders "+2.38", 0 renders "+0.00". */
export function formatZ(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  const sign = v < 0 ? "-" : "+";
  return `${sign}${Math.abs(v).toFixed(2)}`;
}

/** Decimal spread to a two-decimal percent: 0.0712 renders "7.12%". */
export function formatSpreadPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${(v * 100).toFixed(2)}%`;
}

/** VIX close to two decimals: 14.43 renders "14.43". */
export function formatVix(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}
