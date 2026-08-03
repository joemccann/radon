/**
 * Yield Curve Indicator — payload types + pure helpers for the CURVE regime
 * tab. Mirrors the GET /api/yield-curve contract: US Treasury constant-maturity
 * par yields, daily since 1990, with the 10Y-2Y spread and an SPX overlay.
 */

export interface YieldCurvePoint {
  date: string;
  /** 3-month CMT par yield, percent; null when the tenor is unpublished. */
  y3m: number | null;
  y2: number;
  y10: number;
  /** y10 - y2, percentage points. */
  spread: number;
  spx_close: number | null;
}

export interface YieldCurveCurrent {
  date: string;
  y3m: number | null;
  y2: number;
  y10: number;
  spread: number;
}

export interface YieldCurveData {
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  scan_time: string | null;
  source?: string;
  count: number;
  current: YieldCurveCurrent | null;
  series: YieldCurvePoint[];
}

export interface YieldCurveLiveData {
  /** GET contract: an unavailable estimate is HTTP 200 with missing:true. */
  missing?: boolean;
  y10: number | null;
  y3m: number | null;
  spread_10y_3m: number | null;
  asof: string | null;
  source: string | null;
}

/* ─── Formatting ─────────────────────────────────────── */

export function formatSpreadPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function formatYieldPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v.toFixed(2)}%`;
}

/**
 * Spread below this reads as a flat curve (warning); at or above it, a
 * comfortably steep curve (positive). Below zero is an inversion (negative).
 */
export const FLAT_CURVE_THRESHOLD_PCT = 0.25;

export function spreadColor(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "var(--text-muted)";
  if (v < 0) return "var(--negative)";
  if (v < FLAT_CURVE_THRESHOLD_PCT) return "var(--warning)";
  return "var(--positive)";
}

/** "2026-07-31" -> "31 Jul 2026". */
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

/** Eastern wall-clock time for a live as-of instant: "3:35 PM ET". */
export function formatEtTime(iso: string | null | undefined): string {
  if (!iso) return "---";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "---";
  const time = d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
  });
  return `${time} ET`;
}

const MONTH_LABELS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** X-axis tick label for daily data spanning decades: "Jul 2026". */
export function formatDateTick(d: Date): string {
  return `${MONTH_LABELS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}
