/**
 * DIVYIELD — percent of S&P 500 stocks whose trailing 12-month dividend
 * yield is strictly above the 10-Year Treasury yield. Payload types + pure
 * helpers for the DIV YIELD regime tab.
 */

export interface DivYieldSource {
  constituents: string;
  constituents_count: number;
  quote_errors: number;
}

export interface DivYieldLeader {
  ticker: string;
  yield_pct: number;
}

export interface DivYieldPoint {
  date: string;
  pct_above: number;
  count_above: number;
  total: number;
  y10: number;
  approximate: number;
}

export interface DivYieldCurrent {
  date: string;
  pct_above: number;
  count_above: number;
  total: number;
  y10: number;
  leaders: DivYieldLeader[];
}

export interface DivYieldData {
  scan_time: string | null;
  source?: DivYieldSource | null;
  data_date: string | null;
  y10_date?: string | null;
  current: DivYieldCurrent | null;
  series: DivYieldPoint[];
  backfill_cutover: string | null;
  missing?: boolean;
}

// Contract: absent DIVYIELD data is HTTP 200 with missing:true, never a 4xx.
export const MISSING_DIVYIELD: DivYieldData = Object.freeze({
  missing: true,
  scan_time: null,
  data_date: null,
  current: null,
  series: [] as DivYieldPoint[],
  backfill_cutover: null,
});

export type DivYieldRegime = "SCARCE" | "LOW" | "NEUTRAL" | "ELEVATED" | "EXTREME";

const SCARCE_MAX = 5;
const LOW_MAX = 15;
const NEUTRAL_MAX = 35;
const ELEVATED_MAX = 50;

/** Half-open bands from the spec threshold table; boundaries belong to the band above. */
export function divYieldRegimeLabel(pct: number): DivYieldRegime {
  if (pct < SCARCE_MAX) return "SCARCE";
  if (pct < LOW_MAX) return "LOW";
  if (pct < NEUTRAL_MAX) return "NEUTRAL";
  if (pct < ELEVATED_MAX) return "ELEVATED";
  return "EXTREME";
}

export function divYieldRegimeColor(regime: DivYieldRegime): string {
  switch (regime) {
    case "SCARCE": return "var(--negative)";
    case "LOW": return "var(--warning)";
    case "NEUTRAL": return "var(--text-muted)";
    case "ELEVATED": return "var(--positive)";
    case "EXTREME": return "var(--positive)";
  }
}

export function formatDivYieldPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v.toFixed(2)}%`;
}

export function formatDivYieldCount(
  countAbove: number | null | undefined,
  total: number | null | undefined,
): string {
  if (countAbove == null || total == null || !Number.isFinite(countAbove) || !Number.isFinite(total)) {
    return "---";
  }
  return `${Math.round(countAbove)} / ${Math.round(total)}`;
}
