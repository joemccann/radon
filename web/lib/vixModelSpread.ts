/** Personal-use Cboe model spread. Statistics are computed by the daily writer. */
export type SpreadWindow = 20 | 252;
export type SpreadBandState = "ABOVE BAND" | "WITHIN BAND" | "BELOW BAND" | "WARMUP" | "NO VARIATION";

export interface VixModelSpreadEntry {
  date: string;
  vix: number;
  vixeq: number;
  cor1m: number;
  model_vix: number;
  spread: number;
  mean_20: number | null;
  std_20: number | null;
  upper_20: number | null;
  lower_20: number | null;
  z_20: number | null;
  mean_252: number | null;
  std_252: number | null;
  upper_252: number | null;
  lower_252: number | null;
  z_252: number | null;
}

export interface VixModelSpreadData {
  scan_time: string | null;
  missing?: boolean;
  stale?: boolean;
  status: "ok" | "stale_source" | "missing";
  source?: "cboe";
  source_last_modified?: Partial<Record<"VIX" | "VIXEQ" | "COR1M", string | null>>;
  source_dates?: Partial<Record<"VIX" | "VIXEQ" | "COR1M", string>>;
  expected_session?: string | null;
  as_of: string | null;
  data_date: string | null;
  count: number;
  current: VixModelSpreadEntry | null;
  series: VixModelSpreadEntry[];
  /** Retained raw source series support conditional GETs; operator-only. */
  source_series?: Partial<Record<"VIX" | "VIXEQ" | "COR1M", Array<{ date: string; value: number }>>>;
}

function finite(value: number | null | undefined): number | null {
  return value != null && Number.isFinite(value) ? value : null;
}

export function spreadWindowStats(entry: VixModelSpreadEntry | null | undefined, window: SpreadWindow) {
  return {
    mean: finite(entry?.[`mean_${window}`]),
    std: finite(entry?.[`std_${window}`]),
    upper: finite(entry?.[`upper_${window}`]),
    lower: finite(entry?.[`lower_${window}`]),
    z: finite(entry?.[`z_${window}`]),
  };
}

/** Exact band boundaries are within-band; a positive basis alone is not an overshoot. */
export function spreadBandState(entry: VixModelSpreadEntry | null | undefined, window: SpreadWindow): SpreadBandState {
  const { mean, std, upper, lower } = spreadWindowStats(entry, window);
  const spread = finite(entry?.spread);
  if (spread == null || mean == null || std == null || upper == null || lower == null || std < 0) return "WARMUP";
  if (std === 0) return "NO VARIATION";
  if (spread > upper) return "ABOVE BAND";
  if (spread < lower) return "BELOW BAND";
  return "WITHIN BAND";
}

export function formatVolPoints(value: number | null | undefined): string {
  return finite(value)?.toFixed(2) ?? "---";
}

export function formatSpreadZ(value: number | null | undefined): string {
  return finite(value)?.toFixed(2) ?? "---";
}
