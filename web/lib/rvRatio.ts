/**
 * RV-ratio domain lib — types, runtime guard, entry zipping, regime badge
 * mapping, and the session-relative currency check for the section 6
 * payload contract (schema_version 1).
 *
 * All ratio/stats math is computed server-side by scripts/rv_ratio_scan.py
 * and persisted in the snapshot; this module never re-derives statistics.
 * `classifyRatioRegime` is a pure formatter over the payload's precomputed
 * `divergence` string.
 */

import { lastCompletedSessionDate } from "./marketSession";

/** Scan budget shared by the Next route proxy and the client hook (250s covers a cold two-leg Yahoo backfill). */
export const RV_RATIO_SCAN_TIMEOUT_MS = 250_000;

export type RvRatioDivergence = "in_band" | "elevated" | "decoupled" | "compressed";

export interface RvRatioStats {
  high: number;
  low: number;
  avg: number;
  last: number;
  stddev: number;
  last_percentile: number;
  band_upper: number;
  band_lower: number;
  divergence: RvRatioDivergence;
}

export interface RvRatioCoverage {
  first_date: string;
  last_date: string;
  sessions: number;
  complete: boolean;
}

export interface RvRatioSources {
  asset: string[];
  benchmark: string[];
}

export interface RvRatioPayload {
  schema_version: number;
  symbol: string;
  benchmark: string;
  window_sessions: number;
  scan_time: string;
  sources: RvRatioSources;
  dates: string[];
  ratio: number[];
  asset_rv: number[];
  benchmark_rv: number[];
  stats: RvRatioStats;
  coverage: RvRatioCoverage;
  units: string;
  missing: false;
  /** Attached by the Next GET route from the session rule; absent on raw snapshots. */
  stale?: boolean;
}

export interface RvRatioMissingPayload {
  schema_version: number;
  symbol: string;
  missing: true;
  reason?: string;
  scan_time?: string | null;
}

export interface RvRatioEntry {
  date: string;
  ratio: number;
  assetRv: number;
  benchmarkRv: number;
}

export type RvRatioRegimeTone = "neutral" | "caution" | "dislocation" | "neutral-low";

export interface RvRatioRegimeBadge {
  label: string;
  tone: RvRatioRegimeTone;
}

const REGIME_BADGES: Record<RvRatioDivergence, RvRatioRegimeBadge> = {
  in_band: { label: "IN BAND", tone: "neutral" },
  elevated: { label: "ELEVATED", tone: "caution" },
  decoupled: { label: "DECOUPLED", tone: "dislocation" },
  compressed: { label: "COMPRESSED", tone: "neutral-low" },
};

const DIVERGENCES = new Set<string>(Object.keys(REGIME_BADGES));

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function finiteArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every(finite);
}

function stringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === "string");
}

function isRvRatioStats(value: unknown): value is RvRatioStats {
  if (!value || typeof value !== "object") return false;
  const stats = value as Partial<RvRatioStats>;
  return (
    finite(stats.high)
    && finite(stats.low)
    && finite(stats.avg)
    && finite(stats.last)
    && finite(stats.stddev)
    && finite(stats.last_percentile)
    && finite(stats.band_upper)
    && finite(stats.band_lower)
    && typeof stats.divergence === "string"
    && DIVERGENCES.has(stats.divergence)
  );
}

function isRvRatioCoverage(value: unknown): value is RvRatioCoverage {
  if (!value || typeof value !== "object") return false;
  const coverage = value as Partial<RvRatioCoverage>;
  return (
    typeof coverage.first_date === "string"
    && typeof coverage.last_date === "string"
    && finite(coverage.sessions)
    && typeof coverage.complete === "boolean"
  );
}

export function isRvRatioMissingPayload(value: unknown): value is RvRatioMissingPayload {
  if (!value || typeof value !== "object") return false;
  const payload = value as Partial<RvRatioMissingPayload>;
  return payload.missing === true && typeof payload.symbol === "string";
}

export function isRvRatioPayload(value: unknown): value is RvRatioPayload {
  if (!value || typeof value !== "object") return false;
  const payload = value as Partial<RvRatioPayload>;
  return (
    finite(payload.schema_version)
    && typeof payload.symbol === "string"
    && typeof payload.benchmark === "string"
    && finite(payload.window_sessions)
    && typeof payload.scan_time === "string"
    && typeof payload.units === "string"
    && payload.missing === false
    && stringArray(payload.dates)
    && finiteArray(payload.ratio)
    && finiteArray(payload.asset_rv)
    && finiteArray(payload.benchmark_rv)
    && payload.dates.length === payload.ratio.length
    && payload.dates.length === payload.asset_rv.length
    && payload.dates.length === payload.benchmark_rv.length
    && isRvRatioStats(payload.stats)
    && isRvRatioCoverage(payload.coverage)
  );
}

/** Zip the payload's parallel arrays into chart-ready per-session entries. */
export function zipRvRatioEntries(payload: RvRatioPayload): RvRatioEntry[] {
  return payload.dates.map((date, index) => ({
    date,
    ratio: payload.ratio[index],
    assetRv: payload.asset_rv[index],
    benchmarkRv: payload.benchmark_rv[index],
  }));
}

/** Pure formatter: maps the server-computed divergence regime to badge copy + tone. */
export function classifyRatioRegime(stats: Pick<RvRatioStats, "divergence">): RvRatioRegimeBadge {
  return REGIME_BADGES[stats.divergence] ?? REGIME_BADGES.in_band;
}

/**
 * Session-relative currency: a snapshot is current iff it covers the last
 * COMPLETED ET trading session. Saturday reading Friday's scan is fresh
 * despite 20+ wall-clock hours, and intraday yesterday's close is the
 * correct latest point (EOD indicator semantics) — a same-session "stale"
 * flag would fire a pointless scan on every intraday page view.
 */
export function isSnapshotCurrent(
  lastDate: string | null | undefined,
  now: Date = new Date(),
): boolean {
  if (!lastDate) return false;
  return lastDate >= lastCompletedSessionDate(now);
}
