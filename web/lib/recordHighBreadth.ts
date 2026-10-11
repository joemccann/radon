/** TradingView source variant of the supplied three-pane StockCharts chart. */
export interface RecordHighBreadthPoint {
  date: string;
  spx_close: number | null;
  above_200_pct: number | null;
  new_highs: number | null;
  new_lows: number | null;
  net_high_low_per_500: number | null;
  source_timestamps?: Record<string, number | null>;
}
export interface RecordHighBreadthData {
  scan_time: string | null;
  data_date: string | null;
  current: RecordHighBreadthPoint | null;
  series: RecordHighBreadthPoint[];
  degraded?: boolean;
  missing?: boolean;
  source?: {
    provider: string;
    latest_observed_date?: string | null;
    expected_session_date?: string;
    lagging?: boolean;
    history_complete: boolean;
    denominator: number;
    denominator_verified: boolean;
    source_finality: string;
    parity?: { status: string; reference_date: string; reference: { spx_close: number; above_200_pct: number; net_high_low_pct: number }; tradingview: { spx_close: number | null; above_200_pct: number | null; net_high_low_per_500: number | null } | null };
    invalid_observations?: { date: string; field: string; timestamp: number; raw_value: number | string | boolean; reason: string }[];
    missing_sessions?: { date: string; fields: string[] }[];
    coverage?: Record<string, { first_date: string | null; last_date: string | null; returned_count: number; fetched_at: string }>;
  };
}
export const MISSING_RECORD_HIGH_BREADTH: RecordHighBreadthData = {
  missing: true, scan_time: null, data_date: null, current: null, series: [],
};
export function breadthValue(value: number | null | undefined, suffix = ""): string {
  return value != null && Number.isFinite(value) ? `${value.toFixed(2)}${suffix}` : "---";
}
