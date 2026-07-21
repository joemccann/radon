/**
 * Shared RV-ratio payload fixtures (section 6 contract, schema_version 1).
 *
 * All dates are WINDOW-RELATIVE: derived by walking weekday sessions back
 * from the most recent completed ET session (or an injected anchor), never
 * hardcoded — hardcoded dates rot in CI once a freshness window passes them.
 *
 * Stats are derived programmatically from the generated ratio series so
 * assertions against HIGH/LOW/AVG/LAST/STDDEV/PCTL always agree with the
 * arrays, matching the Python producer's definitions (ddof=1, full-history
 * scope, tiered sigma divergence).
 */

import { mostRecentSessionDate } from "@/lib/marketSession";
import type {
  RvRatioDivergence,
  RvRatioMissingPayload,
  RvRatioPayload,
  RvRatioStats,
} from "@/lib/rvRatio";

/** Ratio sessions in a complete payload (2772 aligned closes - 252 window). */
export const RV_RATIO_COMPLETE_SESSIONS = 2520;
/** Ratio sessions in the partial (young-listing) variant. */
export const RV_RATIO_PARTIAL_SESSIONS = 300;

export function addCalendarDays(dateStr: string, days: number): string {
  const d = new Date(`${dateStr}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** 0 (Sun) .. 6 (Sat) for a YYYY-MM-DD date label. */
export function weekdayOf(dateStr: string): number {
  return new Date(`${dateStr}T12:00:00Z`).getUTCDay();
}

export function previousWeekday(dateStr: string): string {
  let d = addCalendarDays(dateStr, -1);
  while (weekdayOf(d) === 0 || weekdayOf(d) === 6) d = addCalendarDays(d, -1);
  return d;
}

/** `count` weekday session dates ending at `endDate` (inclusive), ascending. */
export function sessionDatesEndingAt(count: number, endDate: string): string[] {
  const isWeekend = weekdayOf(endDate) === 0 || weekdayOf(endDate) === 6;
  let d = isWeekend ? previousWeekday(endDate) : endDate;
  const dates: string[] = [];
  while (dates.length < count) {
    dates.push(d);
    d = previousWeekday(d);
  }
  return dates.reverse();
}

function round(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

function deriveStats(ratio: number[]): RvRatioStats {
  const n = ratio.length;
  const high = Math.max(...ratio);
  const low = Math.min(...ratio);
  const avg = ratio.reduce((sum, v) => sum + v, 0) / n;
  const last = ratio[n - 1];
  const variance = n > 1
    ? ratio.reduce((sum, v) => sum + (v - avg) ** 2, 0) / (n - 1)
    : 0;
  const stddev = Math.sqrt(variance);
  const lastPercentile = ratio.filter((v) => v <= last).length / n;
  const divergence: RvRatioDivergence =
    stddev > 0 && last > avg + 2 * stddev ? "decoupled"
    : stddev > 0 && last > avg + stddev ? "elevated"
    : stddev > 0 && last < avg - stddev ? "compressed"
    : "in_band";
  return {
    high: round(high, 4),
    low: round(low, 4),
    avg: round(avg, 4),
    last: round(last, 4),
    stddev: round(stddev, 4),
    last_percentile: round(lastPercentile, 4),
    band_upper: round(avg + stddev, 4),
    band_lower: round(avg - stddev, 4),
    divergence,
  };
}

export interface RvRatioFixtureOptions {
  symbol?: string;
  /** Ratio sessions (payload array length). */
  sessions?: number;
  complete?: boolean;
  /** End the series this many sessions BEFORE the anchor (0 = current). */
  endSessionsAgo?: number;
  /** Explicit end-session anchor; defaults to the most recent completed ET session. */
  endDate?: string;
}

export function buildRvRatioFixture(options: RvRatioFixtureOptions = {}): RvRatioPayload {
  const {
    symbol = "SMH",
    sessions = RV_RATIO_PARTIAL_SESSIONS,
    endSessionsAgo = 0,
  } = options;
  const complete = options.complete ?? sessions >= RV_RATIO_COMPLETE_SESSIONS;

  let end = options.endDate ?? mostRecentSessionDate();
  for (let i = 0; i < endSessionsAgo; i += 1) end = previousWeekday(end);
  const dates = sessionDatesEndingAt(sessions, end);

  const ratio: number[] = [];
  const assetRv: number[] = [];
  const benchmarkRv: number[] = [];
  for (let i = 0; i < sessions; i += 1) {
    const bench = round(14 + 3 * Math.sin(i / 37), 2);
    const r = round(1.05 + 0.22 * Math.sin(i / 41) + 0.12 * Math.sin(i / 173), 4);
    ratio.push(r);
    benchmarkRv.push(bench);
    assetRv.push(round(bench * r, 2));
  }

  const lastDate = dates[dates.length - 1];
  return {
    schema_version: 1,
    symbol,
    benchmark: "SPY",
    window_sessions: 252,
    scan_time: `${lastDate}T21:05:00Z`,
    sources: { asset: ["yahoo", "ib"], benchmark: ["yahoo", "ib"] },
    dates,
    ratio,
    asset_rv: assetRv,
    benchmark_rv: benchmarkRv,
    stats: deriveStats(ratio),
    coverage: {
      first_date: dates[0],
      last_date: lastDate,
      sessions,
      complete,
    },
    units: "x SPY realized vol",
    missing: false,
  };
}

/** ~300-session young-listing payload, complete: false. */
export function buildPartialRvRatioFixture(
  options: Omit<RvRatioFixtureOptions, "sessions" | "complete"> = {},
): RvRatioPayload {
  return buildRvRatioFixture({ ...options, sessions: RV_RATIO_PARTIAL_SESSIONS, complete: false });
}

/** Full ~10y payload: 2520 ratio sessions from 2772 aligned closes, complete: true. */
export function buildCompleteRvRatioFixture(
  options: Omit<RvRatioFixtureOptions, "sessions" | "complete"> = {},
): RvRatioPayload {
  return buildRvRatioFixture({ ...options, sessions: RV_RATIO_COMPLETE_SESSIONS, complete: true });
}

export function buildMissingRvRatioPayload(symbol = "XYZ"): RvRatioMissingPayload {
  return {
    schema_version: 1,
    symbol,
    missing: true,
    reason: "insufficient_history",
    scan_time: new Date().toISOString(),
  };
}
