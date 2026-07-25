/**
 * Shared BPI payload fixtures (section 4 contract, schema_version 1).
 *
 * All dates are WINDOW-RELATIVE (rv-ratio-fixture session-date helpers) —
 * hardcoded dates rot in CI once a freshness window passes them. The series
 * is a deterministic sinusoid engineered to cross the 30 line a known number
 * of times: period 126 sessions between 20 and 70, so 504 sessions carry
 * exactly four UP-crossings through 30 (two inside the trailing 252).
 */

import { mostRecentSessionDate } from "@/lib/marketSession";
import type {
  BpiIndexSymbol,
  BpiMissingIndex,
  BpiPayload,
  BpiResponse,
  BpiState,
} from "@/lib/bpi";
import { previousWeekday, sessionDatesEndingAt } from "./rv-ratio-fixture";

/** Sessions in the full fixture series (~2y trailing window). */
export const BPI_FIXTURE_SESSIONS = 504;

const INDEX_NAMES: Record<BpiIndexSymbol, string> = {
  NDX: "Nasdaq-100",
  SPX: "S&P 500",
  RUT: "Russell 2000",
};

const INDEX_MEMBERS: Record<BpiIndexSymbol, number> = {
  NDX: 100,
  SPX: 500,
  RUT: 1950,
};

function round(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

/** Deterministic BPI value for session index i: 45 + 25 sin(2πi / 126). */
export function bpiFixtureValue(index: number): number {
  return round(45 + 25 * Math.sin((2 * Math.PI * index) / 126), 2);
}

/** UP-crossings through `threshold` in a raw series (the chart-dot rule). */
export function countCrossUps(series: number[], threshold: number): number {
  let count = 0;
  for (let i = 1; i < series.length; i += 1) {
    if (series[i - 1] < threshold && series[i] >= threshold) count += 1;
  }
  return count;
}

export interface BpiFixtureOptions {
  symbol?: BpiIndexSymbol;
  sessions?: number;
  /** Explicit end-session anchor; defaults to the most recent session. */
  endDate?: string;
  /** End the series this many sessions BEFORE the anchor (0 = current). */
  endSessionsAgo?: number;
}

export function buildBpiPayloadFixture(options: BpiFixtureOptions = {}): BpiPayload {
  const {
    symbol = "NDX",
    sessions = BPI_FIXTURE_SESSIONS,
    endSessionsAgo = 0,
  } = options;

  let end = options.endDate ?? mostRecentSessionDate();
  for (let i = 0; i < endSessionsAgo; i += 1) end = previousWeekday(end);
  const dates = sessionDatesEndingAt(sessions, end);
  const values = dates.map((_date, index) => bpiFixtureValue(index));
  const history = dates.map((date, index) => ({ date, bpi: values[index] }));

  const last = values[values.length - 1];
  const prev = values.length > 1 ? values[values.length - 2] : last;
  const members = INDEX_MEMBERS[symbol];
  const state: BpiState = last <= 30 ? "OVERSOLD" : last >= 70 ? "OVERBOUGHT" : "NEUTRAL";
  const lastDate = dates[dates.length - 1];

  return {
    schema_version: 1,
    index_symbol: symbol,
    index_name: INDEX_NAMES[symbol],
    taken_at: `${lastDate}T21:30:00Z`,
    as_of_session: lastDate,
    bpi: last,
    members,
    bullish: Math.round((last / 100) * members),
    state,
    cross_up_30: prev < 30 && last >= 30,
    thresholds: { oversold: 30, overbought: 70 },
    history,
    sources: {
      constituents: "cache",
      member_close_fetches: { yahoo: 0, stored: members },
    },
  };
}

export function buildMissingBpiIndex(symbol: BpiIndexSymbol): BpiMissingIndex {
  return { missing: true, index_symbol: symbol };
}

/** Full three-index response; distinct member counts disambiguate the switcher. */
export function buildBpiResponseFixture(
  options: Omit<BpiFixtureOptions, "symbol"> = {},
): BpiResponse {
  const ndx = buildBpiPayloadFixture({ ...options, symbol: "NDX" });
  return {
    generated_at: ndx.taken_at,
    indices: {
      NDX: ndx,
      SPX: buildBpiPayloadFixture({ ...options, symbol: "SPX" }),
      RUT: buildBpiPayloadFixture({ ...options, symbol: "RUT" }),
    },
  };
}
