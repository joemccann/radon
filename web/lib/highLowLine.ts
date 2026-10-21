/** Published NYSE common-stock-only breadth, accumulated before either interval. */
export type HighLowLineInterval = "daily" | "weekly";
export type HighLowLineZone = "oversold" | "overbought" | "neutral" | "unavailable";
export interface HighLowLineEntry {
  date: string;
  period_start: string;
  period_end: string;
  period_complete: boolean;
  highs: number;
  lows: number;
  net: number;
  cumulative: number;
  rsi14: number | null;
  stoch_rsi14: number | null;
  sar: number | null;
  sar_direction: "rising" | "falling" | null;
  reset_to_zero: boolean;
  source_fractional: boolean;
}
export interface HighLowLineCurrent extends HighLowLineEntry { zone: HighLowLineZone }
export interface HighLowLineSource {
  provider?: string;
  name: string;
  url: string;
  symbols: { highs: "!NEWHINYC"; lows: "!NEWLONYC" };
  history_start: "2003-01-02";
  history_end: string;
  fractional_policy: "preserved";
  fractional_observations: number;
  excluded_non_sessions: string[];
}
export interface HighLowLineData {
  missing: boolean;
  degraded: boolean;
  stale?: boolean;
  scan_time: string | null;
  source_last_modified: string | null;
  data_date: string | null;
  expected_session_date: string | null;
  count: number;
  weekly_count: number;
  current: HighLowLineCurrent | null;
  weekly_current: HighLowLineCurrent | null;
  series: HighLowLineEntry[];
  weekly_series: HighLowLineEntry[];
  source_hash: string | null;
  source: HighLowLineSource | null;
  baseline: { date: "2003-01-02"; value: 0; convention: "before_first_observation" } | null;
  settings: {
    rsi_period: 14; stoch_period: 14; smoothing: "Wilder";
    sar_start: 1; sar_increment: 1; sar_max: 1;
    daily_ohlc: "close_only"; weekly_ohlc: "daily_cumulative_range";
    default_interval: "weekly";
  } | null;
}
export function highLowLineZone(value: number | null): HighLowLineZone {
  if (value === null || !Number.isFinite(value) || value < 0 || value > 1) return "unavailable";
  return value <= 0.2 ? "oversold" : value >= 0.8 ? "overbought" : "neutral";
}
