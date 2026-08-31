/**
 * STREAKS regime tab — payload types + pure display helpers for the
 * GET /api/streaks contract (consecutive daily gains for one symbol).
 * The payload is computed server-side (scripts/utils/streaks.py); this
 * module only formats it. Spec: docs/indicators/streaks.md.
 */

// "rh" is the persisted vocabulary (REL-174, R-485); "robinhood" is accepted
// for cache envelopes written before the rename.
export type StreaksSource = "ib" | "uw" | "rh" | "robinhood" | "yahoo" | "cache" | null;

export interface StreakEntry {
  date: string;
  close: number;
  /** Consecutive gains ending at this session; down/flat close resets to 0. */
  streak: number;
}

export interface StreaksCurrent {
  date: string;
  close: number;
  streak: number;
  day_change_pct: number | null;
}

export interface StreaksStats {
  max_streak: number;
  /** Most recent date whose streak equals max_streak. */
  max_streak_end: string | null;
  runs_total: number;
  /** Runs (completed or in-progress) at or above the current streak; null when the streak is 0. */
  runs_ge_current: number | null;
  avg_run: number | null;
  up_day_pct: number | null;
}

export interface StreaksData {
  symbol: string;
  scan_time: string | null;
  source: StreaksSource;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  count: number;
  first_date: string | null;
  last_date: string | null;
  current: StreaksCurrent | null;
  stats: StreaksStats | null;
  series: StreakEntry[];
}

/* ─── Formatting ─────────────────────────────────────── */

export function formatStreakDays(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "---";
  return `${n} ${n === 1 ? "DAY" : "DAYS"}`;
}

export function streakTone(n: number | null | undefined): "pos" | "mut" {
  return n != null && Number.isFinite(n) && n > 0 ? "pos" : "mut";
}

export function formatDayChangePct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function formatCloseValue(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function formatRunsAtOrAbove(runs: number | null | undefined): string {
  if (runs == null || !Number.isFinite(runs)) return "---";
  return `${runs} ${runs === 1 ? "RUN" : "RUNS"}`;
}

export function formatUpDayPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v.toFixed(1)}%`;
}

const SOURCE_LABELS: Record<Exclude<StreaksSource, null>, string> = {
  ib: "IB",
  uw: "UNUSUAL WHALES",
  rh: "ROBINHOOD",
  robinhood: "ROBINHOOD",
  yahoo: "YAHOO",
  cache: "CACHED",
};

export function sourceLabel(source: StreaksSource | undefined): string {
  if (!source) return "---";
  return SOURCE_LABELS[source] ?? source.toUpperCase();
}
