/** TradingView USI ASK minus BID trade counts. Units are prints, not shares. */
export interface ImbalancePoint {
  date: string;
  ask: number | null;
  bid: number | null;
  batd: number | null;
  imbalance: number | null;
  ema10: number | null;
  spx_close: number | null;
  source_timestamps?: Record<string, number | null>;
}

export interface ImbalanceData {
  scan_time: string | null;
  data_date: string | null;
  current: ImbalancePoint | null;
  series: ImbalancePoint[];
  degraded?: boolean;
  missing?: boolean;
  source?: {
    provider: string;
    units?: string;
    units_verified?: boolean;
    share_volume_symbols_not_used?: string[];
    latest_observed_date?: string | null;
    expected_session_date?: string;
    lagging?: boolean;
    history_complete: boolean;
    source_finality?: string;
    universe_verified?: boolean;
    tick_rule_verified?: boolean;
    session_coverage_verified?: boolean;
    ema_period?: number;
    citation?: string;
    parity_mismatches?: { date: string; ask: number; bid: number; imbalance: number; batd: number }[];
    invalid_observations?: { date: string; field: string; timestamp: number; raw_value: number | string | boolean; reason: string }[];
    missing_sessions?: { date: string; fields: string[] }[];
    coverage?: Record<string, { first_date: string | null; last_date: string | null; returned_count: number; fetched_at?: string }>;
  };
}

export const MISSING_IMBALANCE: ImbalanceData = {
  missing: true, scan_time: null, data_date: null, current: null, series: [],
};

export function formatTrades(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "---";
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 0,
    signDisplay: "exceptZero",
  }).format(value);
}

export function formatEma(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "---";
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 0,
    signDisplay: "exceptZero",
  }).format(Math.round(value));
}

export function formatSpx(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "---";
  return value.toFixed(2);
}
