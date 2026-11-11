/**
 * MSR indicator: payload types + pure helpers for the MSR regime tab.
 * Mirrors the GET /api/msr contract: |1-day % move| of SPX / NDX / RUT
 * divided by the prior close of its Cboe vol index (VIX / VXN / RVX).
 * Spec: docs/indicators/msr.md.
 */

export const MSR_INDEXES = ["SPX", "NDX", "RUT"] as const;

export type MsrIndex = (typeof MSR_INDEXES)[number];

/** One merged series row; null where that index has no session reading. */
export interface MsrRow {
  date: string;
  spx: number | null;
  ndx: number | null;
  rut: number | null;
}

export interface MsrCurrent {
  date: string;
  msr: number;
  move_pct: number;
  close: number;
  iv_prior: number;
  rank: number;
}

export interface MsrWeekMax {
  date: string;
  msr: number;
  move_pct: number;
  iv_prior: number;
  rank: number;
}

export interface MsrStats {
  count: number;
  max: number;
  max_date: string;
  p50: number;
  p90: number;
  p99: number;
}

export interface MsrTopEntry {
  date: string;
  msr: number;
  move_pct: number;
}

export interface MsrIndexSummary {
  iv_symbol: string;
  current: MsrCurrent | null;
  week_max: MsrWeekMax | null;
  stats: MsrStats | null;
  top: MsrTopEntry[];
}

export interface MsrSourceLastModified {
  spx?: string | null;
  vix?: string | null;
  vxn?: string | null;
  rvx?: string | null;
}

export interface MsrData {
  scan_time: string | null;
  start_date: string | null;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  source_last_modified?: MsrSourceLastModified | null;
  close_sources?: Partial<Record<MsrIndex, string[]>> | null;
  indexes: Record<MsrIndex, MsrIndexSummary> | null;
  series: MsrRow[];
}

const MISSING = "---";

function isMissing(v: number | null | undefined): v is null | undefined {
  return v == null || !Number.isFinite(v);
}

/** Three-decimal ratio: "0.262"; "---" for null/non-finite. */
export function formatMsr(v: number | null | undefined): string {
  if (isMissing(v)) return MISSING;
  return v.toFixed(3);
}

/** "#3,736 of 4,779"; "---" when either side is missing. */
export function formatRank(
  rank: number | null | undefined,
  count: number | null | undefined,
): string {
  if (isMissing(rank) || isMissing(count)) return MISSING;
  return `#${rank.toLocaleString("en-US")} of ${count.toLocaleString("en-US")}`;
}

/** Signed two-decimal percent: "-4.36%", "+0.31%"; "---" missing. */
export function formatMovePct(v: number | null | undefined): string {
  if (isMissing(v)) return MISSING;
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

/**
 * Surprise tone, STRICT inequalities: above p99 the vol market badly
 * underpriced the move (negative), above p90 warning, else muted.
 */
export function msrTone(
  v: number | null | undefined,
  stats: Pick<MsrStats, "p90" | "p99"> | null | undefined,
): string {
  if (isMissing(v) || !stats) return "var(--text-muted)";
  if (v > stats.p99) return "var(--negative)";
  if (v > stats.p90) return "var(--warning)";
  return "var(--text-muted)";
}

/** HTTP-date -> "07 Nov 2026"; "---" null; passthrough unparsable. */
export function formatSourceDate(raw: string | null | undefined): string {
  if (!raw) return MISSING;
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

export interface MsrChartRow {
  date: string;
  msr: number;
}

const SERIES_KEY: Record<MsrIndex, "spx" | "ndx" | "rut"> = {
  SPX: "spx",
  NDX: "ndx",
  RUT: "rut",
};

/** One index's readings, rows where it has no value dropped. */
export function buildMsrChartRows(
  series: ReadonlyArray<MsrRow>,
  index: MsrIndex,
): MsrChartRow[] {
  const key = SERIES_KEY[index];
  const rows: MsrChartRow[] = [];
  for (const row of series) {
    const msr = row[key];
    if (isMissing(msr)) continue;
    rows.push({ date: row.date, msr });
  }
  return rows;
}
