/**
 * IV RANK indicator — payload types + pure helpers for the SPY 1M IV Rank
 * regime tab.
 *
 * Mirrors the GET /api/ivrank contract: SPY's 30-day at-the-money implied
 * volatility ranked within its trailing 252-session range. 0 is the cheapest
 * 1M vol of the year, 100 the richest.
 *
 * Descriptive richness/cheapness read only. No validation study was run, so
 * no copy here or in the panel may make a forward-return claim.
 *
 * The constant below is display copy mirrored from the Python job. The UI
 * never recomputes a rank. Spec: docs/indicators/ivrank.md sections B, F.3, G.
 */

/* ─── Constants (display copy only) ──────────────────── */

/** Trailing sessions in the rank window, inclusive of the current session. */
export const RANK_WINDOW = 252;

/* ─── Payload types (spec F.3) ───────────────────────── */

export type IvRankStatus = "ok" | "degraded_uw" | "stale_source" | "missing";
export type IvRankRegime = "SUPPRESSED" | "NORMAL" | "ELEVATED" | "EXTREME";

export interface IvRankEntry {
  date: string;
  /** 30d ATM IV close, annualized decimal (0.1220 = 12.2%). */
  iv: number;
  /** Null for the first RANK_WINDOW-1 rows and degenerate windows. */
  iv_rank: number | null;
  /** Null for the first RANK_WINDOW-1 rows. */
  iv_pct: number | null;
}

export interface IvRankCurrent {
  date: string;
  iv: number | null;
  iv_rank: number | null;
  iv_pct: number | null;
  /** Min/max of the current 252-session window. */
  iv_1y_low: number | null;
  iv_1y_high: number | null;
  /** iv_rank minus the prior non-null iv_rank. */
  rank_change_1d: number | null;
  regime: IvRankRegime | null;
}

export interface IvRankUwCheck {
  date: string;
  iv_rank: number | null;
}

export interface IvRankStats {
  min: number | null;
  p25: number | null;
  median: number | null;
  p75: number | null;
  max: number | null;
  mean: number | null;
  share_suppressed: number | null;
  share_extreme: number | null;
}

export interface IvRankData {
  scan_time: string | null;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  status: IvRankStatus;
  /** Feed that produced the newest row. */
  source?: "ib" | "uw" | null;
  as_of: string | null;
  expected_session?: string | null;
  market_status?: string | null;
  rank_window?: number;
  count: number;
  rank_count?: number;
  current: IvRankCurrent | null;
  /** Advisory UW cross-check of the latest rank, or null. Never an error. */
  uw_check: IvRankUwCheck | null;
  stats: IvRankStats | null;
  series: IvRankEntry[];
}

/* ─── Formatting ─────────────────────────────────────── */

/** One-decimal rank: "10.6"; "---" when unavailable. */
export function formatRank(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(1);
}

/** Decimal IV to a one-decimal percent: 0.12201147 -> "12.2%"; "---" unavailable. */
export function formatIvPercent(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${(v * 100).toFixed(1)}%`;
}

/* ─── Regime bands (spec B.4, strict inequalities) ───── */

/**
 * iv_rank == 20 is NORMAL, == 50 is ELEVATED, == 80 is EXTREME.
 * Null and non-finite readings have no regime.
 */
export function ivrankRegime(v: number | null | undefined): IvRankRegime | null {
  if (v == null || !Number.isFinite(v)) return null;
  if (v < 20) return "SUPPRESSED";
  if (v < 50) return "NORMAL";
  if (v < 80) return "ELEVATED";
  return "EXTREME";
}

/**
 * EXTREME tones as a dislocation, not a fault: rich premium is a structural
 * state of the vol surface, and --negative would be the UI over-claiming.
 */
export function ivrankRegimeColor(regime: IvRankRegime | null | undefined): string {
  if (regime === "ELEVATED") return "var(--warning)";
  if (regime === "EXTREME") return "var(--dislocation)";
  return "var(--text-muted)";
}

/* ─── Chart rows ─────────────────────────────────────── */

export interface IvRankChartRow {
  date: string;
  iv: number;
  /** Nulls survive as nulls: the rank line breaks, it never plots a zero. */
  iv_rank: number | null;
  iv_pct: number | null;
}

export function buildIvRankChartRows(series: ReadonlyArray<IvRankEntry>): IvRankChartRow[] {
  return series.map((entry) => ({
    date: entry.date,
    iv: entry.iv,
    iv_rank: entry.iv_rank,
    iv_pct: entry.iv_pct,
  }));
}
