/**
 * HHLEV — US household leverage, total liabilities as a percent of net worth
 * (Federal Reserve Z.1 Financial Accounts, B.101 family). Payload types +
 * pure helpers for the HH LEV regime tab.
 */

export interface HhLevPoint {
  date: string;
  leverage_pct: number;
}

export interface HhLevCurrent {
  date: string;
  leverage_pct: number;
  liabilities_musd: number;
  net_worth_musd: number;
}

export interface HhLevData {
  scan_time: string | null;
  source_last_modified: string | null;
  data_date: string | null;
  current: HhLevCurrent | null;
  series: HhLevPoint[];
  missing?: boolean;
}

// Contract: absent HHLEV data is HTTP 200 with missing:true, never a 4xx.
export const MISSING_HHLEV: HhLevData = Object.freeze({
  missing: true,
  scan_time: null,
  source_last_modified: null,
  data_date: null,
  current: null,
  series: [] as HhLevPoint[],
});

export type HhLevRegime = "DELEVERAGED" | "MODERATE" | "ELEVATED" | "STRETCHED";

const MODERATE_MIN = 12;
const ELEVATED_MIN = 16;
const STRETCHED_MIN = 20;

/**
 * Half-open bands from the spec threshold table; boundaries belong to the
 * band above. The guard clause pins the explicit DELEVERAGED default for
 * null/NaN — a bare chained-if would leak NaN past every `<` comparison.
 */
export function hhLevRegimeLabel(pct: number | null | undefined): HhLevRegime {
  if (pct == null || !Number.isFinite(pct)) return "DELEVERAGED";
  if (pct >= STRETCHED_MIN) return "STRETCHED";
  if (pct >= ELEVATED_MIN) return "ELEVATED";
  if (pct >= MODERATE_MIN) return "MODERATE";
  return "DELEVERAGED";
}

export function hhLevRegimeColor(regime: HhLevRegime): string {
  switch (regime) {
    case "DELEVERAGED": return "var(--positive)";
    case "MODERATE": return "var(--text-muted)";
    case "ELEVATED": return "var(--warning)";
    case "STRETCHED": return "var(--negative)";
  }
}

/** Quarter-start date to its quarter label: "2026-01-01" renders "2026 Q1". */
export function formatQuarter(date: string | null | undefined): string {
  if (!date) return "---";
  const [year, month] = date.split("-").map(Number);
  if (!Number.isFinite(year) || !Number.isFinite(month)) return "---";
  return `${year} Q${Math.floor((month - 1) / 3) + 1}`;
}

/** $-millions level as one-decimal trillions: 21560050 renders "$21.6T". */
export function formatTrillions(musd: number | null | undefined): string {
  if (musd == null || !Number.isFinite(musd)) return "---";
  return `$${(musd / 1_000_000).toFixed(1)}T`;
}

/** Leverage ratio as a two-decimal percent: 11.78 renders "11.78%". */
export function formatLeveragePct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v.toFixed(2)}%`;
}
