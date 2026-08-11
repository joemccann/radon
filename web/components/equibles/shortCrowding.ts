/**
 * Short crowding / squeeze scoreboard — types and pure display helpers.
 *
 * Every ratio reaching this layer is already a PERCENT in 0-100: the Equibles
 * client renames and rescales fraction fields once at its boundary
 * (shortInterestPercentOfFloat 0.0423 -> shortInterestPctOfFloat 4.23), so
 * nothing here may scale a value again.
 *
 * Short interest belongs to a FINRA settlement date and publishes with a lag.
 * `vintageLabel` is the only sanctioned freshness string for this feature: it
 * is derived from the payload's own settlement date, never from a hardcoded
 * cadence.
 */

export type CrowdingTier = "extreme" | "crowded" | "moderate" | "light" | "unknown";
export type CrowdingIntensity = "extreme" | "high" | "moderate" | "low";

export type ShortCrowdingReadings = {
  /** Gate 1 — convex UPSIDE for long call structures. */
  upside_convexity: CrowdingIntensity;
  /** Gate 3 — fat LEFT tail for short / naked put legs. */
  short_leg_tail_risk: CrowdingIntensity;
};

export type ShortCrowdingBorrowSeam = {
  source: string;
  resolved: boolean;
  fee_rate?: number | null;
  shortable_shares?: number | null;
};

export type ShortCrowdingEntry = {
  ticker: string;
  settlement_date: string | null;
  settlement_age_days: number | null;
  prior_settlement_date: string | null;
  short_position: number | null;
  prior_short_position: number | null;
  change_in_short_position: number | null;
  short_position_change_pct: number | null;
  average_daily_volume: number | null;
  days_to_cover: number | null;
  crowding_tier: string;
  squeeze_score: number | null;
  squeeze_rank: number | null;
  base_score: number | null;
  catalyst_boost: number | null;
  /** Percent 0-100. Read it with `short_interest_pct_basis` — shares and float differ. */
  short_interest_pct: number | null;
  short_interest_pct_basis: string | null;
  market_cap: number | null;
  factors: Record<string, unknown> | null;
  readings: ShortCrowdingReadings;
  borrow: ShortCrowdingBorrowSeam;
};

export type ShortCrowdingBoardRow = {
  ticker: string;
  settlement_date: string | null;
  short_position: number | null;
  days_to_cover: number | null;
  average_daily_volume: number | null;
  short_interest_pct: number | null;
};

export type ShortCrowdingData = {
  missing?: boolean;
  scan_time: string | null;
  count: number;
  latest_settlement_date: string | null;
  entries: ShortCrowdingEntry[];
  board: ShortCrowdingBoardRow[];
};

const UNKNOWN = "---";

function isNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function formatSqueezeScore(value: number | null | undefined): string {
  return isNumber(value) ? value.toFixed(1) : UNKNOWN;
}

export function formatDaysToCover(value: number | null | undefined): string {
  return isNumber(value) ? `${value.toFixed(1)}d` : UNKNOWN;
}

export function formatShares(value: number | null | undefined): string {
  if (!isNumber(value)) return UNKNOWN;
  const magnitude = Math.abs(value);
  if (magnitude >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (magnitude >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (magnitude >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return value.toFixed(0);
}

export function formatChangePct(value: number | null | undefined): string {
  if (!isNumber(value)) return UNKNOWN;
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

export function formatPercent(value: number | null | undefined): string {
  return isNumber(value) ? `${value.toFixed(1)}%` : UNKNOWN;
}

const TIER_COLORS: Record<string, string> = {
  extreme: "var(--fault)",
  crowded: "var(--warning)",
  moderate: "var(--signal-core)",
  light: "var(--text-secondary)",
  unknown: "var(--text-muted)",
};

export function tierColor(tier: string): string {
  return TIER_COLORS[tier] ?? TIER_COLORS.unknown;
}

const INTENSITY_COLORS: Record<string, string> = {
  extreme: "var(--fault)",
  high: "var(--warning)",
  moderate: "var(--signal-core)",
  low: "var(--text-secondary)",
};

export function intensityColor(intensity: string): string {
  return INTENSITY_COLORS[intensity] ?? "var(--text-muted)";
}

function daysSince(settlementDate: string): number | null {
  const settled = Date.parse(`${settlementDate.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(settled)) return null;
  const today = Date.parse(`${new Date().toISOString().slice(0, 10)}T00:00:00Z`);
  return Math.round((today - settled) / 86_400_000);
}

/** The one freshness string for this feature: derived from the data's own settlement. */
export function vintageLabel(
  settlementDate: string | null | undefined,
  ageDays: number | null | undefined,
): string {
  if (!settlementDate) return "SETTLEMENT UNKNOWN";
  const age = isNumber(ageDays) ? ageDays : daysSince(settlementDate);
  if (age === null) return `SETTLED ${settlementDate}`;
  return `SETTLED ${settlementDate} · ${age}D AGO`;
}

/** Shares outstanding and float are different denominators; say which one. */
export function shortInterestBasisLabel(basis: string | null | undefined): string {
  if (basis === "shares") return "Short interest as a percent of shares outstanding";
  if (basis === "float") return "Short interest as a percent of float";
  return "Short interest percent basis unknown";
}

export type ScoreboardSummary = {
  mostCrowded: ShortCrowdingEntry | null;
  topScore: ShortCrowdingEntry | null;
  extremeCount: number;
};

export function buildScoreboardSummary(entries: ShortCrowdingEntry[]): ScoreboardSummary {
  const scored = entries.filter((e) => isNumber(e.squeeze_score));
  const covered = entries.filter((e) => isNumber(e.days_to_cover));

  return {
    mostCrowded: covered.length
      ? covered.reduce((best, e) => ((e.days_to_cover ?? 0) > (best.days_to_cover ?? 0) ? e : best))
      : null,
    topScore: scored.length
      ? scored.reduce((best, e) => ((e.squeeze_score ?? 0) > (best.squeeze_score ?? 0) ? e : best))
      : null,
    extremeCount: entries.filter((e) => e.crowding_tier === "extreme").length,
  };
}
