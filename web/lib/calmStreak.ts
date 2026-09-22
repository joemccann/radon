/* CALM STREAK: consecutive SPX sessions without a >1% intraday band.
 * Types + pure helpers (spec: docs/indicators/calm-streak.md). */

export interface CalmStreakPoint {
  date: string;
  streak: number;
  close: number | null;
}

export interface CalmStreakCurrent extends CalmStreakPoint {
  band_pct: number | null;
}

export interface CalmStreakPeak {
  streak: number;
  date: string | null;
}

export interface CalmStreakWindow extends CalmStreakPeak {
  start: string;
  end: string;
}

export interface CalmStreakStats {
  max: CalmStreakPeak;
  window: CalmStreakWindow;
  percentile: number;
}

export interface CalmStreakData {
  schema_version?: number;
  scan_time: string | null;
  data_date: string | null;
  source_last_modified?: string | null;
  source?: { name: string; url: string } | null;
  threshold_pct?: number;
  current: CalmStreakCurrent | null;
  stats: CalmStreakStats | null;
  series: CalmStreakPoint[];
  missing?: boolean;
  stale?: boolean;
}

export const CALM_STREAK_THRESHOLD_PCT = 1;
export const CALM_PERCENTILE = 90;

export type CalmStreakState = "EXTREME CALM" | "CALM" | "NORMAL";

export function calmStreakStateLabel(streak: number, windowMax: number, percentile: number): CalmStreakState {
  if (streak > windowMax) return "EXTREME CALM";
  if (percentile >= CALM_PERCENTILE) return "CALM";
  return "NORMAL";
}

export function formatBandPct(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? `${v.toFixed(2)}%` : "---";
}
