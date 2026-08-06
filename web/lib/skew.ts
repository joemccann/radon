/**
 * SKEW indicator — payload types + pure helpers for the SKEW regime tab.
 * Mirrors the GET /api/skew contract: daily change in the ratio of SPX
 * ~1-month 25-delta put IV to 25-delta call IV, interpolated in delta from
 * the Unusual Whales chain. Spec: docs/indicators/skew.md.
 */

export interface SkewEntry {
  date: string;
  ratio: number;
  /** Null on the first stored session (no prior ratio to difference). */
  change: number | null;
}

export interface SkewCurrent {
  date: string;
  ratio: number;
  change: number | null;
  put_iv: number;
  call_iv: number;
  expiry: string;
  dte: number;
}

export interface SkewStats {
  high: number;
  low: number;
  avg: number;
  /** Population stddev over all non-null changes. */
  stddev: number;
}

export interface SkewData {
  scan_time: string | null;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  source?: string;
  count: number;
  current: SkewCurrent | null;
  stats: SkewStats | null;
  series: SkewEntry[];
}

/* ─── Formatting ─────────────────────────────────────── */

/** Signed two-decimal daily change: "-0.12", "+0.05"; "---" null/non-finite. */
export function formatSkewChange(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
}

/** Unsigned two-decimal ratio level: "1.29"; "---" null/non-finite. */
export function formatSkewRatio(v: number | null | undefined): string {
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
export function skewChangeColor(
  change: number | null | undefined,
  stddev: number | null | undefined,
): string {
  if (change == null || !Number.isFinite(change)) return "var(--text-muted)";
  if (stddev == null || !Number.isFinite(stddev) || stddev <= 0) return "var(--text-muted)";
  return Math.abs(change) > 2 * stddev ? "var(--warning)" : "var(--text-muted)";
}

/* ─── Chart rows ─────────────────────────────────────── */

export type SkewChartView = "change" | "level";

export interface SkewChartRow {
  date: string;
  /** Active-view series; nulls preserved (the chart skips them). */
  value: number | null;
}

export function buildSkewChartRows(
  series: ReadonlyArray<SkewEntry>,
  view: SkewChartView,
): SkewChartRow[] {
  return series.map((entry) => ({
    date: entry.date,
    value: view === "change" ? entry.change : entry.ratio,
  }));
}
