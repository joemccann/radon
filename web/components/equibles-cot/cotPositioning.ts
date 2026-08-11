/**
 * CFTC COT cross-asset positioning — payload types + pure helpers for the COT
 * regime tab. Mirrors the GET /api/equibles-cot-positioning contract.
 *
 * Positions are contract counts as published by the CFTC. The metric is the net
 * non-commercial (speculator) position ranked against its own trailing history;
 * extremes are contrarian, and commercials are the hedgers taking the other side.
 *
 * Freshness copy is derived from report_date (the Tuesday the report measures),
 * never from a hardcoded cadence string.
 */

export type CotCrowding = "CROWDED LONG" | "CROWDED SHORT" | "NEUTRAL" | "UNKNOWN";
export type CotBias = "BEARISH" | "BULLISH" | "NEUTRAL";

export interface CotWeek {
  report_date: string;
  open_interest: number | null;
  net_commercial: number | null;
  net_noncommercial: number | null;
  net_noncommercial_pct_oi: number | null;
}

export interface CotContract {
  alias: string;
  market_code: string;
  name: string | null;
  category: string | null;
  report_date: string;
  weeks: number;
  open_interest: number | null;
  net_noncommercial: number | null;
  net_commercial: number | null;
  net_noncommercial_pct_oi: number | null;
  net_noncommercial_change: number | null;
  /** Null until the trailing window holds min_stats_weeks of reports. */
  percentile: number | null;
  z_score: number | null;
  crowding: CotCrowding;
  contrarian_bias: CotBias;
  series: CotWeek[];
}

export interface CotBoardRow {
  market_code: string | null;
  name: string | null;
  category: string | null;
  report_date: string;
  open_interest: number | null;
  net_commercial: number | null;
  net_noncommercial: number | null;
  net_noncommercial_pct_oi: number | null;
}

export interface CotPositioningData {
  scan_time: string | null;
  report_date: string | null;
  count: number;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  lookback_days?: number;
  min_stats_weeks?: number;
  extremes?: { high: number; low: number };
  contracts: CotContract[];
  market: CotBoardRow[];
}

/* ─── Formatting ─────────────────────────────────────── */

function isNumber(v: number | null | undefined): v is number {
  return v != null && Number.isFinite(v);
}

/** Signed contract counts: "+150.0k" / "-42.3k" / "+940". */
export function formatContracts(v: number | null | undefined): string {
  if (!isNumber(v)) return "---";
  if (v === 0) return "0";
  const sign = v > 0 ? "+" : "-";
  const magnitude = Math.abs(v);
  if (magnitude >= 1000) return `${sign}${(magnitude / 1000).toFixed(1)}k`;
  return `${sign}${Math.round(magnitude)}`;
}

export function formatPercentile(v: number | null | undefined): string {
  return isNumber(v) ? v.toFixed(1) : "---";
}

export function formatZScore(v: number | null | undefined): string {
  if (!isNumber(v)) return "---";
  return `${v >= 0 ? "+" : "-"}${Math.abs(v).toFixed(2)}`;
}

export function formatShareOfOi(v: number | null | undefined): string {
  if (!isNumber(v)) return "---";
  return `${v >= 0 ? "+" : "-"}${Math.abs(v).toFixed(1)}%`;
}

/** "2026-08-04" -> "04 Aug 2026". */
export function formatReportDate(raw: string | null | undefined): string {
  if (!raw) return "---";
  const parsed = new Date(`${raw.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

const DAY_MS = 86_400_000;

/** Whole days between the measured Tuesday and now; null when undated. */
export function reportAgeDays(raw: string | null | undefined, now: Date): number | null {
  if (!raw) return null;
  const reported = new Date(`${raw.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(reported.getTime())) return null;
  return Math.floor((now.getTime() - reported.getTime()) / DAY_MS);
}

/**
 * "04 Aug 2026 · 7d old" — the age is measured, so the label stays honest when
 * a release slips instead of asserting a cadence the data may not have met.
 */
export function formatReportFreshness(raw: string | null | undefined, now: Date): string {
  if (!raw) return "---";
  const age = reportAgeDays(raw, now);
  if (age == null) return formatReportDate(raw);
  return `${formatReportDate(raw)} · ${age <= 0 ? "today" : `${age}d old`}`;
}

/* ─── Tones ──────────────────────────────────────────── */

const CROWDING_COLORS: Record<CotCrowding, string> = {
  "CROWDED LONG": "var(--warning)",
  "CROWDED SHORT": "var(--warning)",
  NEUTRAL: "var(--text-secondary)",
  UNKNOWN: "var(--text-muted)",
};

/** Both extremes warn: a crowded book is fragile in either direction. */
export function crowdingColor(crowding: CotCrowding): string {
  return CROWDING_COLORS[crowding] ?? "var(--text-muted)";
}

export const CONTRARIAN_TONE: Record<CotBias, string> = {
  BEARISH: "var(--negative)",
  BULLISH: "var(--positive)",
  NEUTRAL: "var(--text-muted)",
};

/* ─── Chart rows ─────────────────────────────────────── */

export interface CotChartRow {
  date: string;
  spec: number | null;
  comm: number | null;
}

export function buildCotChartRows(series: ReadonlyArray<CotWeek>): CotChartRow[] {
  return series.map((week) => ({
    date: week.report_date,
    spec: week.net_noncommercial,
    comm: week.net_commercial,
  }));
}

/* ─── Range presets (weeks, not sessions) ────────────── */

export type CotRangeSlug = "6m" | "1y" | "3y" | "all";

export interface CotRangePreset {
  slug: CotRangeSlug;
  label: string;
  weeks: number;
}

export const COT_RANGE_PRESETS: ReadonlyArray<CotRangePreset> = [
  { slug: "6m", label: "6M", weeks: 26 },
  { slug: "1y", label: "1Y", weeks: 52 },
  { slug: "3y", label: "3Y", weeks: 156 },
  { slug: "all", label: "All", weeks: Number.POSITIVE_INFINITY },
];

/** Inclusive [start, end] slice indices for a preset over `total` weeks. */
export function cotPresetRange(slug: CotRangeSlug, total: number): [number, number] {
  if (total === 0) return [0, 0];
  const preset = COT_RANGE_PRESETS.find((p) => p.slug === slug);
  const weeks = Math.min(preset?.weeks ?? Number.POSITIVE_INFINITY, total);
  return [Math.max(0, total - weeks), total - 1];
}

/* ─── Selection ──────────────────────────────────────── */

/** The requested contract, else the first one; never a stranded empty panel. */
export function selectContract(
  contracts: ReadonlyArray<CotContract>,
  alias: string,
): CotContract | null {
  if (contracts.length === 0) return null;
  return contracts.find((c) => c.alias === alias) ?? contracts[0];
}
