/**
 * ATS venue-share types and pure display helpers.
 *
 * Every metric arrives already derived from scripts/fetch_equibles_ats_venue_share.py:
 * percent fields are 0-100, z-scores are measured against a trailing 52-week
 * window, and the classification is the joint reading of ATS venue share and
 * block print size. Nothing here re-scales or re-derives; it only formats.
 *
 * ats_share_pct is ATS volume as a share of OFF-EXCHANGE volume. Equibles
 * publishes no consolidated (all-venue) denominator, so off_exchange_share_pct
 * is null until one exists and must not be presented as if it were.
 */

export type AtsClassification = "accumulation" | "distribution" | "neutral";

export type AtsVenueShareRow = {
  ticker?: string;
  week_start_date: string;
  ats_volume: number | null;
  ats_trade_count: number | null;
  non_ats_otc_volume: number | null;
  non_ats_otc_trade_count: number | null;
  total_off_exchange_volume: number | null;
  ats_share_pct: number | null;
  avg_ats_print_size: number | null;
  avg_non_ats_print_size: number | null;
  ats_share_z: number | null;
  avg_ats_print_size_z: number | null;
  short_volume_pct: number | null;
  consolidated_volume: number | null;
  consolidated_volume_source: string | null;
  off_exchange_share_pct: number | null;
  classification: AtsClassification;
  classification_reason: string | null;
  z_observations?: number;
};

export type AtsVenueShareThresholds = {
  accumulation_z: number;
  distribution_z: number;
  z_window_weeks: number;
  min_history_weeks: number;
};

export type AtsVenueShareError = { ticker: string; error: string };

export type AtsVenueShareData = {
  missing?: boolean;
  scan_time: string | null;
  source?: string;
  count: number;
  tickers: string[];
  week_start_date: string | null;
  thresholds: AtsVenueShareThresholds | null;
  current: AtsVenueShareRow[];
  series: Record<string, AtsVenueShareRow[]>;
  errors: AtsVenueShareError[];
};

export const PLACEHOLDER = "---";

const CLASSIFICATION_COLORS: Record<AtsClassification, string> = {
  accumulation: "var(--positive)",
  distribution: "var(--negative)",
  neutral: "var(--text-muted)",
};

function isNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function formatSharePct(value: number | null | undefined): string {
  return isNumber(value) ? `${value.toFixed(1)}%` : PLACEHOLDER;
}

export function formatZ(value: number | null | undefined): string {
  if (!isNumber(value)) return PLACEHOLDER;
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

export function formatGap(value: number | null | undefined): string {
  if (!isNumber(value)) return PLACEHOLDER;
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)} pp`;
}

export function formatPrintSize(value: number | null | undefined): string {
  return isNumber(value) ? Math.round(value).toLocaleString("en-US") : PLACEHOLDER;
}

export function classificationLabel(classification: AtsClassification): string {
  return classification.toUpperCase();
}

export function classificationColor(classification: AtsClassification): string {
  return CLASSIFICATION_COLORS[classification] ?? CLASSIFICATION_COLORS.neutral;
}

/** ATS share minus short-volume share, in percentage points. Both measure the
 *  same off-exchange tape, so the gap is the interesting case: dark-pool volume
 *  running ahead of short volume is buying rather than hedging. */
export function venueDivergence(row: AtsVenueShareRow): number | null {
  if (!isNumber(row.ats_share_pct) || !isNumber(row.short_volume_pct)) return null;
  return row.ats_share_pct - row.short_volume_pct;
}

/** Freshness copy comes from the week the payload carries, never from an
 *  assumed refresh interval (the FINRA file itself only moves weekly). */
export function weekLabel(week: string | null | undefined): string {
  return week ? `WEEK OF ${week}` : PLACEHOLDER;
}

export function countByClassification(
  rows: AtsVenueShareRow[],
): Record<AtsClassification, number> {
  const tally: Record<AtsClassification, number> = {
    accumulation: 0,
    distribution: 0,
    neutral: 0,
  };
  for (const row of rows) {
    if (row.classification in tally) tally[row.classification] += 1;
  }
  return tally;
}
