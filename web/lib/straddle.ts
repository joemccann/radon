/**
 * STRADDLE indicator — payload types + pure helpers for the STRADDLE regime
 * tab. Mirrors the GET /api/straddle contract: daily signed ratio of the SPX
 * close-to-close move to the option-implied 1-day straddle at the prior close
 * (Cboe SPX + VIX1D daily series). Spec: docs/indicators/straddle.md.
 */

export interface StraddleEntry {
  date: string;
  spx_close: number;
  vix1d_close: number;
  /** Null on the first session (no prior close to measure a move against). */
  ratio: number | null;
}

export interface StraddleCurrent {
  date: string;
  ratio: number | null;
  move_pct: number;
  implied_straddle_pct: number;
  spx_close: number;
  vix1d_prior: number;
}

export interface StraddleStats {
  high: number;
  low: number;
  avg: number;
  /** Population stddev over all non-null ratios. */
  stddev: number;
  /** Share of sessions with |ratio| strictly beyond 1 (fraction, not %). */
  hit_rate: number;
}

export interface StraddleSourceLastModified {
  spx: string | null;
  vix1d: string | null;
}

export interface StraddleData {
  scan_time: string | null;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  source_last_modified?: StraddleSourceLastModified | null;
  count: number;
  current: StraddleCurrent | null;
  stats: StraddleStats | null;
  series: StraddleEntry[];
}

/* ─── Formatting ─────────────────────────────────────── */

/** Signed two-decimal ratio: "+3.78", "-4.97"; "---" for null/non-finite. */
export function formatRatio(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
}

/**
 * Breakeven tone, STRICT inequalities: beyond +1 the realized move beat the
 * straddle upside, beyond -1 the downside; the boundary (exactly ±1), the
 * inside band, and null all read muted.
 */
export function ratioColor(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "var(--text-muted)";
  if (v > 1) return "var(--positive)";
  if (v < -1) return "var(--negative)";
  return "var(--text-muted)";
}

/** Implied 1-day straddle as a percent of spot: "0.47%"; "---" null. */
export function formatStraddlePct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v.toFixed(2)}%`;
}

/** Breakeven hit rate, fraction in: 0.367675 -> "36.8%"; "---" null. */
export function formatHitRate(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${(v * 100).toFixed(1)}%`;
}

/** HTTP-date -> "05 Aug 2026"; "---" null; passthrough unparsable. */
export function formatSourceDate(raw: string | null | undefined): string {
  if (!raw) return "---";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/* ─── Chart rows ─────────────────────────────────────── */

export interface StraddleChartRow {
  date: string;
  /** Right-axis series; nulls preserved (the chart skips them). */
  ratio: number | null;
  /** Left-axis SPX overlay. */
  spx: number;
}

export function buildStraddleChartRows(
  series: ReadonlyArray<StraddleEntry>,
): StraddleChartRow[] {
  return series.map((entry) => ({
    date: entry.date,
    ratio: entry.ratio,
    spx: entry.spx_close,
  }));
}
