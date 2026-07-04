/**
 * Margin Debt Acceleration Indicator — payload types + pure helpers for the
 * MARGIN regime tab. Mirrors the GET /api/margin-debt contract: FINRA debit
 * balances Jan 1997+, NYSE member-firm series 1959-1996 ratio-adjusted at the
 * splice. All levels on the wire are $ millions.
 */

export type MarginDebtSource = "nyse_legacy" | "finra";

export interface MarginDebtEntry {
  date: string;
  /** Raw debit balances, $ millions. */
  level: number;
  free_credit_cash: number | null;
  free_credit_margin: number | null;
  source: MarginDebtSource;
  /** Null for the first 12 months and the 12 splice-seam months (1997). */
  level_yoy_pct: number | null;
  /** Splice-adjusted level (legacy months scaled by the splice ratio). */
  level_display: number | null;
  /** Debit balances minus free credit balances. */
  net_level: number | null;
  /** CPI-deflated level; null when normalization is unavailable. */
  level_real: number | null;
  /** Level as a percent of nominal GDP; null when GDP lags the month. */
  level_pct_gdp: number | null;
  spx_close: number | null;
}

export interface MarginDebtSplice {
  legacy_source: string;
  ratio: number;
  first_finra_month: string;
}

export interface MarginDebtCurrent {
  date: string;
  level: number;
  level_yoy_pct: number | null;
}

export interface MarginDebtData {
  scan_time: string | null;
  source_last_modified?: string | null;
  count: number;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  splice: MarginDebtSplice | null;
  normalization: { available: boolean } | null;
  current: MarginDebtCurrent | null;
  series: MarginDebtEntry[];
}

/* ─── Formatting ─────────────────────────────────────── */

/** $ millions -> "$1,415.6bn". */
export function formatLevelBn(levelMm: number | null | undefined): string {
  if (levelMm == null || !Number.isFinite(levelMm)) return "---";
  const bn = levelMm / 1000;
  return `$${bn.toLocaleString("en-US", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}bn`;
}

export function formatYoyPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/**
 * YoY growth >= this reads as froth, not health — every major top
 * (2000, 2007, 2021) printed >50% margin-debt acceleration first.
 */
export const YOY_FROTH_THRESHOLD_PCT = 50;

export function yoyColor(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "var(--text-muted)";
  if (v >= YOY_FROTH_THRESHOLD_PCT) return "var(--warning)";
  if (v >= 0) return "var(--positive)";
  return "var(--negative)";
}

/** HTTP-date (or ISO) -> "16 Jun 2026"; falls back to the raw string. */
export function formatSourceDate(raw: string | null | undefined): string {
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

/** X-axis tick label for monthly data spanning decades: "Jan 1997". */
export function formatMonthTick(d: Date): string {
  return `${MONTH_LABELS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/* ─── Smoothing ──────────────────────────────────────── */

/**
 * Trailing 3-month moving average: mean of the non-null values in the
 * [i-2, i] window. Null months stay null (a seam gap must not invent data);
 * null neighbours shrink the window instead of poisoning it.
 */
export function movingAverage3(values: ReadonlyArray<number | null>): Array<number | null> {
  return values.map((v, i) => {
    if (v == null || !Number.isFinite(v)) return null;
    const window = values
      .slice(Math.max(0, i - 2), i + 1)
      .filter((x): x is number => x != null && Number.isFinite(x));
    return window.reduce((sum, x) => sum + x, 0) / window.length;
  });
}

/* ─── Right-series views ─────────────────────────────── */

export type MarginRightView = "yoy" | "level" | "net" | "gdp" | "real";

export interface MarginRightViewSpec {
  slug: MarginRightView;
  label: string;
  /** CPI/GDP-derived views only render when normalization.available. */
  requiresNormalization: boolean;
  scaleType: "log" | "linear";
  format: (v: number) => string;
  extract: (entry: MarginDebtEntry) => number | null;
}

function toBn(mm: number | null): number | null {
  return mm == null || !Number.isFinite(mm) ? null : mm / 1000;
}

function formatBnAxis(v: number): string {
  const digits = Math.abs(v) >= 100 ? 0 : 1;
  return `$${v.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}bn`;
}

export const MARGIN_RIGHT_VIEWS: ReadonlyArray<MarginRightViewSpec> = [
  {
    slug: "yoy",
    label: "YOY %",
    requiresNormalization: false,
    scaleType: "linear",
    format: formatYoyPct,
    extract: (e) => e.level_yoy_pct,
  },
  {
    slug: "level",
    label: "LEVEL $BN",
    requiresNormalization: false,
    scaleType: "log",
    format: formatBnAxis,
    extract: (e) => toBn(e.level_display),
  },
  {
    slug: "net",
    label: "NET $BN",
    requiresNormalization: false,
    scaleType: "linear",
    format: formatBnAxis,
    extract: (e) => toBn(e.net_level),
  },
  {
    slug: "gdp",
    label: "% OF GDP",
    requiresNormalization: true,
    scaleType: "linear",
    format: (v) => `${v.toFixed(1)}%`,
    extract: (e) => e.level_pct_gdp,
  },
  {
    slug: "real",
    label: "REAL $BN",
    requiresNormalization: true,
    scaleType: "linear",
    format: formatBnAxis,
    extract: (e) => toBn(e.level_real),
  },
];

export function marginRightViews(normalizationAvailable: boolean): MarginRightViewSpec[] {
  return MARGIN_RIGHT_VIEWS.filter((v) => normalizationAvailable || !v.requiresNormalization);
}

export function marginViewSpec(slug: MarginRightView): MarginRightViewSpec {
  const spec = MARGIN_RIGHT_VIEWS.find((v) => v.slug === slug);
  if (!spec) throw new Error(`Unknown margin right view: ${slug}`);
  return spec;
}

/* ─── Chart rows ─────────────────────────────────────── */

export interface MarginChartRow {
  date: string;
  spx: number | null;
  right: number | null;
}

export function buildMarginChartRows(
  series: ReadonlyArray<MarginDebtEntry>,
  view: MarginRightViewSpec,
  smooth: boolean,
): MarginChartRow[] {
  const raw = series.map(view.extract);
  const right = smooth ? movingAverage3(raw) : raw;
  return series.map((entry, i) => ({
    date: entry.date,
    spx: entry.spx_close,
    right: right[i] ?? null,
  }));
}

/* ─── Range presets (months, not sessions) ───────────── */

export type MarginRangeSlug = "1y" | "5y" | "10y" | "all";

export interface MarginRangePreset {
  slug: MarginRangeSlug;
  label: string;
  months: number;
}

export const MARGIN_RANGE_PRESETS: ReadonlyArray<MarginRangePreset> = [
  { slug: "1y", label: "1Y", months: 12 },
  { slug: "5y", label: "5Y", months: 60 },
  { slug: "10y", label: "10Y", months: 120 },
  { slug: "all", label: "All", months: Number.POSITIVE_INFINITY },
];

/** Inclusive [start, end] slice indices for a preset over `total` months. */
export function marginPresetRange(slug: MarginRangeSlug, total: number): [number, number] {
  if (total === 0) return [0, 0];
  const preset = MARGIN_RANGE_PRESETS.find((p) => p.slug === slug);
  const months = Math.min(preset?.months ?? Number.POSITIVE_INFINITY, total);
  return [Math.max(0, total - months), total - 1];
}
