/**
 * Panic Proxy — equal-weight mean of 252-session z-scores of four Cboe
 * series (VIX, VVIX, VIX/VIX3M, SKEW). Display types + formatters only.
 * The UI never recomputes a z-score, the composite, the change, or the rank.
 *
 * Display types + formatters only. Spec: docs/indicators/panic-index.md.
 */

/* ─── Constants (display copy only) ──────────────────── */

export const Z_WINDOW = 252;
export const RANK_WINDOW = 2520;
export const RANK_MIN_ROWS = 504;
export const ALERT_SIGMA = 3.0;
export const ALERT_TOP_N = 10;

export const DISCLAIMER = "Radon reconstruction - not the Goldman Sachs index";

export const SOURCE_FOOTNOTE =
  "Radon Panic Proxy. Equal-weight mean of 252-session z-scores of Cboe VIX, VVIX, VIX/VIX3M and SKEW. Goldman Sachs' Panic Index formula is proprietary; this is Radon's reconstruction from its stated inputs and is not comparable in level.";

export const INFO_TOOLTIP =
  "Radon Panic Proxy - not Goldman's index. An equal-weight average of the " +
  "252-session z-scores of four Cboe series that Goldman names as the inputs to " +
  "its Panic Index: VIX (S&P vol), VVIX (VIX vol), VIX/VIX3M (S&P term structure) " +
  "and the Cboe SKEW index (S&P skew). Higher is more panic. The chart plots the " +
  "one-day change: a large negative print is a panic unwind, a large positive " +
  "print is a panic surge. Source: CBOE (VIX, VIX3M, VVIX, SKEW).";

/* ─── Payload types ──────────────────────────────────── */

export interface PanicIndexLeg {
  value: number | null;
  z: number | null;
  vix3m?: number | null;
}

export interface PanicIndexLegs {
  vix: PanicIndexLeg;
  vvix: PanicIndexLeg;
  ts: PanicIndexLeg;
  skew: PanicIndexLeg;
  skew25d?: PanicIndexLeg | null;
}

export interface PanicIndexCurrent {
  date: string;
  level: number | null;
  delta_1d: number | null;
  delta_z: number | null;
  delta_std_10y: number | null;
  rank_decline_10y: number | null;
  rank_surge_10y: number | null;
  rank_n: number | null;
  legs: PanicIndexLegs;
}

export interface PanicIndexStats {
  high: number;
  high_date: string;
  low: number;
  low_date: string;
  avg: number;
  stddev: number;
}

export interface PanicIndexPoint {
  date: string;
  vix: number;
  vix3m: number;
  vvix: number;
  ts: number;
  skew: number;
  z_vix: number | null;
  z_vvix: number | null;
  z_ts: number | null;
  z_skew: number | null;
  level: number | null;
  delta_1d: number | null;
  skew25d?: number | null;
  z_skew25d?: number | null;
}

export interface PanicIndexAlert {
  last_fired_date: string | null;
  last_fired_kind: string | null;
}

export interface PanicIndexSourceStamps {
  vix?: string | null;
  vix3m?: string | null;
  vvix?: string | null;
  skew?: string | null;
}

export interface PanicIndexData {
  scan_time: string | null;
  source_last_modified: PanicIndexSourceStamps | null;
  data_date: string | null;
  expected_session?: string | null;
  lag_days?: number | null;
  status?: string | null;
  count: number;
  delta_count: number;
  dropped_dates?: string[];
  z_window?: number;
  rank_window?: number;
  current: PanicIndexCurrent | null;
  stats: PanicIndexStats | null;
  alert: PanicIndexAlert | null;
  series: PanicIndexPoint[];
  missing?: boolean;
}

/** Contract: absent Panic Proxy data is HTTP 200 with missing:true, never a 4xx. */
export const MISSING_PANIC_INDEX: PanicIndexData = Object.freeze({
  missing: true,
  scan_time: null,
  source_last_modified: null,
  data_date: null,
  count: 0,
  delta_count: 0,
  current: null,
  stats: null,
  alert: null,
  series: [] as PanicIndexPoint[],
});

/* ─── Display helpers (no math) ──────────────────────── */

function finite(v: number | null | undefined): v is number {
  return v != null && Number.isFinite(v);
}

function signed(v: number, digits: number): string {
  const body = v.toFixed(digits);
  return v > 0 ? `+${body}` : body;
}

/** Composite level to two signed decimals: -0.6256 renders "-0.63". */
export function formatLevel(v: number | null | undefined): string {
  if (!finite(v)) return "---";
  return signed(v, 2);
}

/** One-day change to two signed decimals: -0.6269 renders "-0.63". */
export function formatDelta(v: number | null | undefined): string {
  if (!finite(v)) return "---";
  return signed(v, 2);
}

/** Z-score to one signed decimal: -1.75 renders "-1.8". */
export function formatZ(v: number | null | undefined): string {
  if (!finite(v)) return "---";
  return signed(v, 1);
}

/** Rank cell: "#87 of 2509". */
export function formatRank(rank: number | null | undefined, n: number | null | undefined): string {
  if (!finite(rank) || !finite(n) || n < RANK_MIN_ROWS) return "---";
  return `#${rank} of ${n}`;
}

/** Index close to two decimals. */
export function formatClose(v: number | null | undefined): string {
  if (!finite(v)) return "---";
  return v.toFixed(2);
}

/** Term-structure ratio to four decimals. */
export function formatTs(v: number | null | undefined): string {
  if (!finite(v)) return "---";
  return v.toFixed(4);
}

/**
 * Tone for a 1d change. Strict inequalities: a panic surge is not good news,
 * so `var(--positive)` is never used. Boundaries stay muted.
 */
export function deltaTone(
  delta: number | null | undefined,
  stddev: number | null | undefined,
): string {
  if (!finite(delta) || !finite(stddev) || stddev <= 0) return "var(--text-muted)";
  const abs = Math.abs(delta);
  if (abs > 3 * stddev) return "var(--negative)";
  if (abs > 2 * stddev) return "var(--warning)";
  return "var(--text-muted)";
}

export function rankKind(delta: number | null | undefined): "decline" | "surge" | "none" {
  if (!finite(delta) || delta === 0) return "none";
  return delta < 0 ? "decline" : "surge";
}
