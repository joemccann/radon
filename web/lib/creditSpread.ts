/**
 * Credit-equity divergence — payload types + pure helpers for the CREDIT
 * regime tab. HYG vs S&P 500, 168-session window. ICE CCC OAS is not stored.
 */

export const LOOKBACK_SESSIONS = 168;
export const NEAR_HIGH_RATIO = 0.97;

export type CreditRegime = "divergent" | "coupled" | "risk-off" | "credit-lead";

export interface CreditSpreadPoint {
  date: string;
  hyg_close: number;
  spx_close: number;
}

export interface CreditSpreadCurrent {
  date: string;
  hyg_close: number;
  spx_close: number;
  hyg_ret: number | null;
  spx_ret: number | null;
  regime: CreditRegime;
  near_high: boolean;
}

export interface CreditSpreadData {
  missing?: boolean;
  scan_time: string | null;
  source?: string;
  count: number;
  current: CreditSpreadCurrent | null;
  series: CreditSpreadPoint[];
}

export function formatPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  const pct = v * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

export function formatSessionDate(raw: string | null | undefined): string {
  if (!raw) return "---";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

const MONTH_LABELS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

export function formatDateTick(d: Date): string {
  return `${MONTH_LABELS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

export function classifyRegime(
  spxRet: number | null | undefined,
  hygRet: number | null | undefined,
): CreditRegime {
  if (spxRet == null || hygRet == null || !Number.isFinite(spxRet) || !Number.isFinite(hygRet)) {
    return "coupled";
  }
  if (spxRet > 0 && hygRet < 0) return "divergent";
  if (spxRet < 0 && hygRet < 0) return "risk-off";
  if (spxRet < 0 && hygRet > 0) return "credit-lead";
  return "coupled";
}

export function regimeColor(regime: CreditRegime | null | undefined): string {
  if (regime === "divergent") return "var(--warning)";
  if (regime === "coupled") return "var(--positive)";
  if (regime === "risk-off") return "var(--negative)";
  return "var(--text-muted)";
}

export function isNearHigh(
  last: number,
  high: number,
  ratio: number = NEAR_HIGH_RATIO,
): boolean {
  return Number.isFinite(last) && Number.isFinite(high) && last >= ratio * high;
}
