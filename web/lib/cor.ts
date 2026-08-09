/**
 * COR indicator — payload types + pure helpers for the COR regime tab.
 * Mirrors the GET /api/cor contract: Cboe SPX implied correlation family
 * (COR1M/COR3M/COR6M/COR1Y daily closes). Spec: docs/indicators/cor.md.
 */

export type CorTenorKey = "cor1m" | "cor3m" | "cor6m" | "cor1y";

export interface CorEntry {
  date: string;
  /** Null where a tenor is missing that date (COR3M has scattered gaps). */
  cor1m: number | null;
  cor3m: number | null;
  cor6m: number | null;
  cor1y: number | null;
}

export interface CorTenorStats {
  high: number;
  low: number;
  avg: number;
  /** Population stddev over the tenor's non-null closes. */
  stddev: number;
  /** Share of the tenor's non-null closes strictly below the latest close. */
  percentile: number;
}

export interface CorCurrent {
  date: string;
  cor1m: number | null;
  cor3m: number | null;
  cor6m: number | null;
  cor1y: number | null;
  /** cor1y minus cor1m; null when either leg is null. */
  term_spread: number | null;
  /** Latest non-null close minus the previous non-null close, per tenor. */
  change_1d: Record<CorTenorKey, number | null>;
}

export type CorSourceLastModified = Partial<Record<CorTenorKey, string | null>>;

export interface CorData {
  scan_time: string | null;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  source_last_modified?: CorSourceLastModified | null;
  count: number;
  current: CorCurrent | null;
  stats: Record<CorTenorKey, CorTenorStats | null> | null;
  series: CorEntry[];
}

export type CorRegime = "COMPRESSED" | "STRESS" | "NEUTRAL";

export const TENOR_OPTIONS: ReadonlyArray<{ key: CorTenorKey; label: string }> = [
  { key: "cor1m", label: "1M" },
  { key: "cor3m", label: "3M" },
  { key: "cor6m", label: "6M" },
  { key: "cor1y", label: "1Y" },
];

/* ─── Formatting ─────────────────────────────────────── */

/** Index points, two decimals: "12.16"; "---" for null/non-finite. */
export function formatCor(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}

/** Signed two-decimal change: "+0.98", "-1.07"; "---" for null/non-finite. */
export function formatCorChange(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
}

/** Fraction -> one-decimal percent: 0.0083 -> "0.8%"; "---" null. */
export function formatPercentile(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${(v * 100).toFixed(1)}%`;
}

/**
 * Regime off the all-history percentile, STRICT inequalities: below the 10th
 * percentile implied correlation is compressed (dispersion crowding), above
 * the 90th systemic stress is already in the price; the boundaries, the middle
 * band, and null all read NEUTRAL.
 */
export function corRegime(percentile: number | null | undefined): CorRegime {
  if (percentile == null || !Number.isFinite(percentile)) return "NEUTRAL";
  if (percentile < 0.1) return "COMPRESSED";
  if (percentile > 0.9) return "STRESS";
  return "NEUTRAL";
}

export function corRegimeColor(regime: CorRegime): string {
  if (regime === "COMPRESSED") return "var(--warning)";
  if (regime === "STRESS") return "var(--negative)";
  return "var(--text-muted)";
}

/* ─── Derivations ────────────────────────────────────── */

/** Term spread = cor1y minus cor1m; null when either leg is null. */
export function termSpread(
  current: { cor1m: number | null; cor1y: number | null } | null | undefined,
): number | null {
  if (!current) return null;
  const { cor1m, cor1y } = current;
  if (cor1m == null || !Number.isFinite(cor1m)) return null;
  if (cor1y == null || !Number.isFinite(cor1y)) return null;
  return cor1y - cor1m;
}

/** Newest parseable RFC-1123 stamp of the four files; null when none parse. */
export function latestSourceStamp(
  stamps: CorSourceLastModified | null | undefined,
): string | null {
  if (!stamps) return null;
  let newest: string | null = null;
  let newestTime = Number.NEGATIVE_INFINITY;
  for (const raw of Object.values(stamps)) {
    if (!raw) continue;
    const time = new Date(raw).getTime();
    if (Number.isNaN(time)) continue;
    if (time > newestTime) {
      newestTime = time;
      newest = raw;
    }
  }
  return newest;
}

/* ─── Chart rows ─────────────────────────────────────── */

export interface CorChartRow {
  date: string;
  /** Selected tenor's close; nulls preserved (the chart skips them). */
  value: number | null;
}

export function buildCorChartRows(
  series: ReadonlyArray<CorEntry>,
  tenor: CorTenorKey,
): CorChartRow[] {
  return series.map((entry) => ({
    date: entry.date,
    value: entry[tenor],
  }));
}
