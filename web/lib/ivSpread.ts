/**
 * IV SPREAD indicator - payload types + pure helpers for the NDX vs SPX 1M
 * IV Spread regime tab.
 *
 * Mirrors the GET /api/iv-spread contract: NDX 30-day at-the-money implied
 * volatility minus SPX 30-day ATM implied volatility, in volatility points,
 * read against its own full stored history (mean and sample stdev).
 *
 * Descriptive relative-premium read only. No validation study was run, so
 * no copy here or in the panel may make a forward-return claim.
 *
 * The constants below are display copy mirrored from the Python job. The UI
 * never recomputes a spread, z-score or percentile.
 * Spec: docs/indicators/iv-spread.md sections B.4, F.3, G.2.
 */

/* ─── Constants (display copy only, spec B.1 / B.4) ──── */

/** z below this is COMPRESSED. */
export const Z_COMPRESSED_MAX = -1;
/** z at or above Z_COMPRESSED_MAX and below this is NORMAL. */
export const Z_NORMAL_MAX = 1;
/** z at or above Z_NORMAL_MAX and below this is ELEVATED; at or above is EXTREME. */
export const Z_ELEVATED_MAX = 2;

/* ─── Payload types (spec F.3) ───────────────────────── */

export type IvSpreadStatus = "ok" | "stale_source" | "missing";
export type IvSpreadRegime = "COMPRESSED" | "NORMAL" | "ELEVATED" | "EXTREME";

export interface IvSpreadEntry {
  date: string;
  /** SPX 30d ATM IV close, annualized decimal (0.1210 = 12.1%). */
  spx_iv: number;
  /** NDX 30d ATM IV close, annualized decimal. */
  ndx_iv: number;
  /** (ndx_iv - spx_iv) x 100; null only for a session the bad-print gate excluded. */
  spread: number | null;
}

export interface IvSpreadCurrent {
  date: string;
  spx_iv: number | null;
  ndx_iv: number | null;
  spread: number | null;
  /** (spread - mean) / stdev over the full non-null history. */
  z_score: number | null;
  /** Share of history strictly below the latest spread, 0-100. */
  pctile: number | null;
  /** Spread minus the prior non-null spread. */
  change_1d: number | null;
  regime: IvSpreadRegime | null;
}

export interface IvSpreadStats {
  count: number | null;
  high: number | null;
  high_date: string | null;
  low: number | null;
  low_date: string | null;
  mean: number | null;
  stdev: number | null;
  last: number | null;
}

export interface IvSpreadExcluded {
  date: string;
  leg: "SPX" | "NDX";
  iv: number | null;
  prev_iv: number | null;
  next_iv: number | null;
}

export interface IvSpreadData {
  scan_time: string | null;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  status: IvSpreadStatus;
  source?: "ib" | null;
  as_of: string | null;
  expected_session?: string | null;
  market_status?: string | null;
  count: number;
  spread_count?: number;
  dropped_unpaired?: number;
  current: IvSpreadCurrent | null;
  stats: IvSpreadStats | null;
  excluded: IvSpreadExcluded[];
  series: IvSpreadEntry[];
}

/* ─── Formatting (spec G.2) ──────────────────────────── */

/** Two-decimal vol points, sign only when negative: 5.481468 -> "5.48"; "---" unavailable. */
export function formatSpread(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}

/** Decimal IV to a one-decimal percent: 0.1758578 -> "17.6%"; "---" unavailable. */
export function formatIvPercent(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${(v * 100).toFixed(1)}%`;
}

/** Signed two decimals: 0.104002 -> "+0.10", 0 -> "+0.00"; "---" unavailable. */
export function formatZ(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v < 0 ? "" : "+"}${v.toFixed(2)}`;
}

/* ─── Regime bands (spec B.4, strict inequalities) ───── */

/**
 * z == -1 is NORMAL, == 1 is ELEVATED, == 2 is EXTREME.
 * Null and non-finite readings have no regime.
 */
export function ivSpreadRegime(z: number | null | undefined): IvSpreadRegime | null {
  if (z == null || !Number.isFinite(z)) return null;
  if (z < Z_COMPRESSED_MAX) return "COMPRESSED";
  if (z < Z_NORMAL_MAX) return "NORMAL";
  if (z < Z_ELEVATED_MAX) return "ELEVATED";
  return "EXTREME";
}

/**
 * EXTREME tones as a dislocation, not a fault: a blown-out tech premium is a
 * structural state of the vol surface, and --negative would be the UI
 * over-claiming.
 */
export function ivSpreadRegimeColor(regime: IvSpreadRegime | null | undefined): string {
  if (regime === "ELEVATED") return "var(--warning)";
  if (regime === "EXTREME") return "var(--dislocation)";
  return "var(--text-muted)";
}

/* ─── Chart rows ─────────────────────────────────────── */

export interface IvSpreadChartRow {
  date: string;
  spx_iv: number;
  ndx_iv: number;
  /** Nulls survive as nulls: the spread line gaps, it never plots a zero. */
  spread: number | null;
}

export function buildIvSpreadChartRows(series: ReadonlyArray<IvSpreadEntry>): IvSpreadChartRow[] {
  return series.map((entry) => ({
    date: entry.date,
    spx_iv: entry.spx_iv,
    ndx_iv: entry.ndx_iv,
    spread: entry.spread,
  }));
}
