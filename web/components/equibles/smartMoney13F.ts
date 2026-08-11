/**
 * 13F smart-money depth layer — types and pure display helpers.
 *
 * Percent fields are ALWAYS 0-100. EquiblesClient rewrites the API's fraction
 * fields to unambiguous '...Pct' keys at its boundary, so nothing here ever
 * multiplies by 100 or divides by it.
 *
 * Freshness copy is derived from `disclosure.age_days` — never hardcode a
 * cadence string. 13F is quarterly with a statutory 45-day filing lag, which
 * is why the payload carries `context_only` and `gate_note`: read as
 * positioning context, never as an entry trigger (it fails Gate 2 on its own).
 */

export type ChangeType = "NEW" | "ADDED" | "TRIMMED" | "EXITED" | "HELD" | string;
export type Staleness = "fresh" | "current" | "stale";
export type Tone = "pos" | "neg" | "mut" | "ok" | "warn";

export type Disclosure = {
  report_date: string;
  quarter: string;
  filed_by: string;
  age_days: number;
  staleness: Staleness;
  cadence: string;
  lag_days: number;
  context_only: boolean;
  gate_note: string;
};

export type Holder = {
  cik: string;
  institution: string;
  shares: number | null;
  value_usd: number | null;
  ownership_pct: number | null;
  change_in_shares: number | null;
  change_type: ChangeType | null;
};

export type SuperInvestorPosition = {
  investor: string;
  institution: string;
  cik: string;
  shares: number | null;
  value_usd: number | null;
  ownership_pct: number | null;
  change_type: ChangeType | null;
};

export type OwnershipQuarter = {
  report_date: string | null;
  filer_count: number | null;
  filer_count_delta: number | null;
  shares_held: number | null;
  value_usd: number | null;
  ownership_pct: number | null;
  new_positions: number | null;
  exited_positions: number | null;
};

export type Participant = {
  cik: string;
  institution: string;
  shares_changed: number | null;
  value_usd: number | null;
};

export type Headline = {
  filer_count: number | null;
  filer_count_delta: number | null;
  new_positions: number | null;
  exited_positions: number | null;
  ownership_pct: number | null;
  top_holders: Holder[];
  super_investors: SuperInvestorPosition[];
};

export type MarketContext = {
  report_date: string | null;
  most_held_rank: number | null;
  most_held_filers: number | null;
  most_held_filers_delta: number | null;
  in_new_positions_bucket: boolean;
  in_sold_out_bucket: boolean;
};

export type SmartMoney13FData = {
  ticker: string;
  missing?: boolean;
  scan_time: string | null;
  report_date: string | null;
  disclosure: Disclosure | null;
  headline: Headline | null;
  holders: Holder[];
  holder_count: number;
  ownership_series: OwnershipQuarter[];
  activity: { buyers: Participant[]; sellers: Participant[] };
  super_investors: SuperInvestorPosition[];
  market_context: MarketContext | null;
};

export const UNAVAILABLE = "---";

function isNumber(value: number | null | undefined): value is number {
  return value != null && Number.isFinite(value);
}

export function formatCount(value: number | null | undefined): string {
  if (!isNumber(value)) return UNAVAILABLE;
  return value.toLocaleString("en-US");
}

export function formatDelta(value: number | null | undefined): string {
  if (!isNumber(value)) return UNAVAILABLE;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toLocaleString("en-US")}`;
}

const MAGNITUDES: Array<[number, string]> = [
  [1e12, "T"],
  [1e9, "B"],
  [1e6, "M"],
  [1e3, "K"],
];

function scaled(value: number): string {
  const magnitude = Math.abs(value);
  for (const [threshold, suffix] of MAGNITUDES) {
    if (magnitude >= threshold) return `${(value / threshold).toFixed(1)}${suffix}`;
  }
  return value.toLocaleString("en-US");
}

export function formatUsdCompact(value: number | null | undefined): string {
  if (!isNumber(value)) return UNAVAILABLE;
  if (value === 0) return "$0";
  return `$${scaled(value)}`;
}

export function formatShares(value: number | null | undefined): string {
  if (!isNumber(value)) return UNAVAILABLE;
  return scaled(value);
}

export function formatPct(value: number | null | undefined): string {
  if (!isNumber(value)) return UNAVAILABLE;
  return `${value.toFixed(2)}%`;
}

const CHANGE_TONES: Record<string, Tone> = {
  NEW: "pos",
  ADDED: "pos",
  TRIMMED: "neg",
  EXITED: "neg",
  HELD: "mut",
};

export function changeTone(changeType: ChangeType | null | undefined): Tone {
  if (!changeType) return "mut";
  return CHANGE_TONES[changeType.toUpperCase()] ?? "mut";
}

const STALENESS_TONES: Record<string, Tone> = {
  fresh: "ok",
  current: "mut",
  stale: "warn",
};

/** An aged 13F is expected, not a fault — the worst tone available is a warn. */
export function stalenessTone(staleness: Staleness | null | undefined): Tone {
  if (!staleness) return "mut";
  return STALENESS_TONES[staleness] ?? "mut";
}

export function vintageHeadline(disclosure: Disclosure | null | undefined): string {
  if (!disclosure) return "Vintage unknown";
  return `${disclosure.quarter} 13F - filed by ${disclosure.filed_by} - ${disclosure.age_days} days old`;
}
