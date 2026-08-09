/**
 * SKEW 2D indicator — payload types + pure helpers for the SKEW 2D regime tab.
 * Mirrors the GET /api/skew2d contract: two-session change in the ratio of
 * SPX ~1-month 25-delta put IV to 25-delta call IV. Spec: docs/indicators/skew2d.md.
 */

export interface Skew2dEntry {
  date: string;
  ratio: number;
  /** Null on the first two stored sessions (fewer than 2 prior ratios). */
  change: number | null;
}

export interface Skew2dCurrent {
  date: string;
  ratio: number;
  change: number | null;
  put_iv: number;
  call_iv: number;
  expiry: string;
  dte: number;
  /** Far bracket of the 30d constant-maturity interpolation; absent on
   * rows priced from a single usable monthly. */
  expiry_far?: string | null;
  dte_far?: number | null;
  /** Snapshot-only current-session observation; never a finalized DB row. */
  is_intraday?: boolean;
  /** UTC observation timestamp for an intraday row. */
  as_of?: string;
}

export interface Skew2dStats {
  high: number;
  low: number;
  avg: number;
  /** Population stddev over all non-null 2d changes. */
  stddev: number;
}

export interface Skew2dData {
  scan_time: string | null;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  source?: string;
  count: number;
  current: Skew2dCurrent | null;
  stats: Skew2dStats | null;
  series: Skew2dEntry[];
  market_status?: "open" | "closed";
}

export const SKEW2D_LIVE_MAX_AGE_MS = 3 * 60_000;

export function isFreshIntradaySkew2d(
  current: Skew2dCurrent | null | undefined,
  nowMs = Date.now(),
): boolean {
  if (!current?.is_intraday || !current.as_of) return false;
  const asOfMs = Date.parse(current.as_of);
  if (!Number.isFinite(asOfMs)) return false;
  const age = nowMs - asOfMs;
  return age >= 0 && age <= SKEW2D_LIVE_MAX_AGE_MS;
}

/* ─── Formatting ─────────────────────────────────────── */

/** Signed two-decimal daily change: "-0.12", "+0.05"; "---" null/non-finite. */
export function formatSkew2dChange(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
}

/** Unsigned two-decimal ratio level: "1.29"; "---" null/non-finite. */
export function formatSkew2dRatio(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}

/** IV fraction to one-decimal percent: 0.1596 -> "16.0%"; "---" null. */
export function formatIvPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${(v * 100).toFixed(1)}%`;
}

/** Signed one-decimal z-score: "-3.0", "+1.3"; "---" null/non-finite. */
export function formatZ(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}`;
}

/* ─── Derivations ────────────────────────────────────── */

/** change / stddev; null when either input is missing or stddev <= 0. */
export function zScore(
  change: number | null | undefined,
  stddev: number | null | undefined,
): number | null {
  if (change == null || !Number.isFinite(change)) return null;
  if (stddev == null || !Number.isFinite(stddev) || stddev <= 0) return null;
  return change / stddev;
}

/**
 * Tail-event tone, STRICT inequality: a change strictly beyond 2 x stddev in
 * either direction reads var(--warning); the exact 2-sigma boundary, the
 * inside band, nulls, and degenerate stddev all read var(--text-muted).
 */
export function skew2dChangeColor(
  change: number | null | undefined,
  stddev: number | null | undefined,
): string {
  if (change == null || !Number.isFinite(change)) return "var(--text-muted)";
  if (stddev == null || !Number.isFinite(stddev) || stddev <= 0) return "var(--text-muted)";
  return Math.abs(change) > 2 * stddev ? "var(--warning)" : "var(--text-muted)";
}

/* ─── Chart rows ─────────────────────────────────────── */

export type Skew2dChartView = "change" | "level";

export interface Skew2dChartRow {
  date: string;
  /** Active-view series; nulls preserved (the chart skips them). */
  value: number | null;
}

export function buildSkew2dChartRows(
  series: ReadonlyArray<Skew2dEntry>,
  view: Skew2dChartView,
): Skew2dChartRow[] {
  return series.map((entry) => ({
    date: entry.date,
    value: view === "change" ? entry.change : entry.ratio,
  }));
}
