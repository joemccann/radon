/**
 * VIXCOR indicator — payload types + pure helpers for the VIX-COR regime tab.
 *
 * Mirrors the GET /api/vixcor contract: the rolling 20-session Pearson
 * correlation between the Cboe VIX close and the Cboe 3-month SPX implied
 * correlation close, computed on price LEVELS over the two series' shared
 * session calendar.
 *
 * Descriptive regime read, never a forecast. The validation study rejected the
 * predictive framing: forward VIX drawup after a breakdown comes in below the
 * all-session mean at every horizon tested, while on the median the two are
 * indistinguishable at 42 sessions (event +31.8% vs base +29.3%, Mann-Whitney
 * p=0.68), so the mean-only gap is a right-skew artefact of the base
 * distribution rather than a refutation at that horizon. A low reading marks a
 * quiet, range-bound VIX tape. Spec: docs/indicators/vixcor.md section 0.
 *
 * The constants below are display copy mirrored from the Python module. The UI
 * never recomputes a window, and never scopes a statistic to the visible slice.
 */

/* ─── Constants (display copy only) ──────────────────── */

/** Trailing joined sessions in the correlation window, inclusive of today. */
export const CORR_WINDOW = 20;
/** corr20 strictly below this opens or confirms a breakdown episode. */
export const BREAKDOWN_TRIGGER = 0.25;
/** Hysteresis: an episode stays open while corr20 is strictly below this. */
export const BREAKDOWN_EXIT = 0.3;
/** Band edge between LOOSENING and COUPLED. */
export const LOOSENING_FLOOR = 0.5;

/* ─── Payload types ──────────────────────────────────── */

export type VixcorStatus = "ok" | "holding" | "stale_parent" | "missing";
export type VixcorRegime = "DECOUPLED" | "LOOSENING" | "COUPLED";

export interface VixcorEntry {
  date: string;
  vix_close: number;
  cor3m_close: number;
  /** Null for the first WINDOW-1 joined sessions and degenerate windows. */
  corr20: number | null;
  /** Membership in a detected breakdown episode, precomputed by the job. */
  episode: boolean;
}

export interface VixcorEpisode {
  /** First session of the merged run below BREAKDOWN_TRIGGER. */
  trigger: string;
  /** First session of the merged run below BREAKDOWN_EXIT. */
  start: string;
  end: string;
  sessions: number;
  trough: number | null;
  trough_date: string | null;
  corr_at_trigger?: number | null;
  vix_at_trigger?: number | null;
  /** True while the run reaches the latest session and is still sub-exit. */
  open: boolean;
  /** Forward VIX drawup keyed by horizon in joined sessions. */
  forward?: Record<string, number | null>;
}

export interface VixcorCurrent {
  date: string;
  vix_close: number | null;
  cor3m_close: number | null;
  corr20: number | null;
  /** corr20 minus the prior non-null corr20. */
  change_1d: number | null;
  /** Share of non-null corr20 strictly below the latest reading. */
  percentile: number | null;
  regime: VixcorRegime | null;
  /** Sample stddev over mean of the VIX across the same 20 joined sessions. */
  vix_cov_20d: number | null;
  episode: VixcorEpisode | null;
}

export interface VixcorStats {
  min: number | null;
  p01: number | null;
  p05: number | null;
  p10: number | null;
  p25: number | null;
  median: number | null;
  p75: number | null;
  p90: number | null;
  p95: number | null;
  p99: number | null;
  max: number | null;
  mean: number | null;
  stddev: number | null;
  share_below_zero: number | null;
  share_below_trigger: number | null;
  /** Mean 20-day VIX coefficient of variation inside sub-trigger windows. */
  vix_cov_breakdown: number | null;
  /** The same measure inside coupled windows, the comparison it loses to. */
  vix_cov_coupled: number | null;
}

export interface VixcorForwardBucket {
  n: number;
  mean_drawup: number | null;
  median_drawup: number | null;
  p_higher: number | null;
  p_drawup_20: number | null;
}

export interface VixcorForwardStats {
  horizons: number[];
  /** Over resolved episode triggers. */
  event: Record<string, VixcorForwardBucket>;
  /** Over every emittable session: the null the UI must show alongside. */
  base: Record<string, VixcorForwardBucket>;
}

export interface VixcorData {
  scan_time: string | null;
  /** GET contract: absent data is HTTP 200 with missing:true, never a 4xx. */
  missing?: boolean;
  source_last_modified?: { vix?: string | null } | null;
  status: VixcorStatus;
  as_of: string | null;
  parent_as_of?: string | null;
  vix_as_of?: string | null;
  expected_session?: string | null;
  /** Joined sessions the parent COR3M series trails the expected session by. */
  lag_sessions?: number | null;
  market_status?: string | null;
  window?: number;
  count: number;
  corr_count?: number;
  current: VixcorCurrent | null;
  stats: VixcorStats | null;
  episodes: VixcorEpisode[];
  forward_stats: VixcorForwardStats | null;
  series: VixcorEntry[];
}

/* ─── Formatting ─────────────────────────────────────── */

/** Signed two-decimal correlation: "+0.01", "-0.53"; "---" when unavailable. */
export function formatCorr(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
}

/** Two-decimal index level: "14.25"; "---" when unavailable. */
export function formatVix(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}

/** Fraction to one-decimal percent: 0.0181 -> "1.8%"; "---" when unavailable. */
export function formatPercentile(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${(v * 100).toFixed(1)}%`;
}

/** Signed one-decimal percent drawup: 0.0392 -> "+3.9%"; "---" unresolved. */
export function formatDrawup(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  const pct = v * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

/* ─── Regime bands ───────────────────────────────────── */

/**
 * Strict inequalities throughout: exactly BREAKDOWN_TRIGGER is not a trigger
 * and exactly LOOSENING_FLOOR is already coupled.
 */
export function vixcorRegime(v: number | null | undefined): VixcorRegime | null {
  if (v == null || !Number.isFinite(v)) return null;
  if (v < BREAKDOWN_TRIGGER) return "DECOUPLED";
  if (v < LOOSENING_FLOOR) return "LOOSENING";
  return "COUPLED";
}

/**
 * DECOUPLED tones as a dislocation, not a fault: it is a structural state of
 * the two series, and --negative would be the UI over-claiming.
 */
export function vixcorRegimeColor(regime: VixcorRegime | null | undefined): string {
  if (regime === "DECOUPLED") return "var(--dislocation)";
  if (regime === "LOOSENING") return "var(--warning)";
  return "var(--text-muted)";
}

/* ─── Chart rows ─────────────────────────────────────── */

export interface VixcorChartRow {
  date: string;
  vix_close: number;
  cor3m_close: number;
  /** Nulls survive as nulls: the lower line breaks, it never plots a zero. */
  corr20: number | null;
  episode: boolean;
}

/** Inclusive [start, end] slice of the joined series, clamped to bounds. */
export function buildVixcorChartRows(
  series: ReadonlyArray<VixcorEntry>,
  range: [number, number],
): VixcorChartRow[] {
  const total = series.length;
  if (total === 0) return [];
  const end = Math.min(range[1], total - 1);
  const start = Math.max(0, Math.min(range[0], end));
  return series.slice(start, end + 1).map((entry) => ({
    date: entry.date,
    vix_close: entry.vix_close,
    cor3m_close: entry.cor3m_close,
    corr20: entry.corr20,
    episode: entry.episode,
  }));
}

/* ─── Episode helpers ────────────────────────────────── */

/** The live unresolved episode, or null when the last one has recovered. */
export function openEpisode(
  episodes: ReadonlyArray<VixcorEpisode> | null | undefined,
): VixcorEpisode | null {
  if (!episodes) return null;
  return episodes.find((episode) => episode.open) ?? null;
}

/** The most recent resolved episode, newest last in the payload's ordering. */
export function lastResolvedEpisode(
  episodes: ReadonlyArray<VixcorEpisode> | null | undefined,
): VixcorEpisode | null {
  if (!episodes) return null;
  for (let i = episodes.length - 1; i >= 0; i -= 1) {
    if (!episodes[i].open) return episodes[i];
  }
  return null;
}

/** Episodes whose extent overlaps the visible date window. */
export function visibleEpisodes(
  episodes: ReadonlyArray<VixcorEpisode> | null | undefined,
  firstDate: string | null,
  lastDate: string | null,
): VixcorEpisode[] {
  if (!episodes || !firstDate || !lastDate) return [];
  return episodes.filter((episode) => episode.start <= lastDate && episode.end >= firstDate);
}

/**
 * Parent-lag sub-label, derived from the payload's session count rather than
 * any hardcoded cadence copy.
 */
export function parentLagCopy(lagSessions: number | null | undefined): string {
  // R-195: `?? 0` collapsed null (the job could NOT determine parent lag)
  // into 0 (the parent is current), and both branches then returned the
  // literal "DAILY SERIES SINCE 2006-01" — a hardcoded cadence claim, which
  // the repo's UI copy rule forbids, standing in for an unknown.
  if (lagSessions == null || !Number.isFinite(lagSessions)) return "PARENT LAG UNKNOWN";
  if (lagSessions <= 0) return "PARENT CURRENT";
  if (lagSessions === 1) return "PARENT 1 SESSION BEHIND";
  return `PARENT ${lagSessions} SESSIONS BEHIND`;
}
