/**
 * Pure evaluation core for GET /api/probe/freshness (DUR-16).
 *
 * The Tier-3 off-box prober calls the route every few minutes and records
 * tick/scan/journal freshness into external_probe_runs. The contract shape
 * is FIXED (the prober is built against it):
 *
 *   { generated_at, market_state, database_ok, database_failures,
 *     checks: { relay_tick, vcg_scan, gex_scan, journal },
 *     all_fresh, journal_last_fill }
 *
 * Each check is { applicable, age_secs, fresh }. applicable=false (and
 * fresh=null) when the market-state windows say the writer is expected
 * quiet — NEVER a uniform staleness model (feedback_service_health_staleness,
 * feedback_extended_market_state_window). all_fresh ANDs the applicable
 * checks and is null when none apply.
 *
 * journal_last_fill and journal_gap_warning are additive. The Tier-3
 * classifier ignores unknown top-level keys and rejects any extra key
 * inside a check, so neither warning pages.
 *
 * No DB, no clock, no fetch in this module — the route supplies raw inputs
 * and `now`, so the whole matrix is unit-testable with a pinned clock.
 */

import {
  getFreshnessWindowMs,
  getMarketStateFromDate,
  getServiceCategory,
  isStale,
  type MarketState,
} from "./serviceHealthWindows";
import { parseScanTime } from "./parseScanTime";

export type ProbeCheck = {
  applicable: boolean;
  age_secs: number | null;
  fresh: boolean | null;
};

export type ProbeFreshnessPayload = {
  generated_at: string;
  market_state: MarketState;
  checks: {
    relay_tick: ProbeCheck;
    vcg_scan: ProbeCheck;
    gex_scan: ProbeCheck;
    journal: ProbeCheck;
  };
  all_fresh: boolean | null;
  database_ok: boolean;
  database_failures: string[];
  /** Last journal fill. Informational. Does not affect all_fresh. */
  journal_last_fill: JournalLastFill;
  /** gap-sli self-error or stale heartbeat. Informational. Does not affect all_fresh. */
  journal_gap_warning: string | null;
};

/** One service_health row for a journal pipeline writer. */
export type JournalHealthRow = {
  state: string;
  last_error: string | null;
  updated_at: string | null;
  /** When state is not ok: service_health_events.created_at of the transition
   * that opened the current non-ok streak. Ok heartbeats do not write events,
   * so this is not "last ok" while state is ok. */
  notOkSince?: string | null;
};

export type JournalCheckInput = {
  writtenAt: string | null;
  journalSync: JournalHealthRow | null;
  journalGapSli: JournalHealthRow | null;
};

export type JournalLastFill = {
  age_secs: number | null;
  /** Set when the newest fill is older than {@link JOURNAL_LAST_FILL_QUIET_MS}. */
  warning: string | null;
};

/** The relay's service_health row, as read from Turso. */
export type RelayHealthRow = {
  state: string;
  /** JSON detail written by the relay's RTH tick heartbeat (DUR-16):
   * { heartbeat: "tick", last_tick_at, tick_age_secs, active_subscriptions }. */
  last_error: string | null;
  updated_at: string | null;
};

export type ProbeFreshnessInputs = {
  relayRow: RelayHealthRow | null;
  /** MAX(scan_time) from vcg_snapshots — may be naive Python isoformat. */
  vcgScanTime: string | null;
  /** MAX(scan_time) from gex_snapshots. */
  gexScanTime: string | null;
  /** MAX(written_at) from journal. Informational. Does not page. */
  journalWrittenAt: string | null;
  /** service_health row `journal-sync`. */
  journalSync: JournalHealthRow | null;
  /** service_health row `journal-gap-sli`. Detail lives in last_error. */
  journalGapSli: JournalHealthRow | null;
  /** Query labels that failed, distinct from valid empty result sets. */
  databaseFailures?: string[];
};

/** Tick age beyond this during RTH (with active demand) is not fresh.
 * The relay's own stale ladder trips at 45s; 120s adds reconnect headroom. */
export const RELAY_TICK_FRESH_SECS = 120;

/** The relay heartbeats every 60s during RTH (scripts/ib_realtime_server.js).
 * A heartbeat older than this means the relay PROCESS is silent — not fresh
 * regardless of the tick timestamp it last reported. */
export const RELAY_HEARTBEAT_STALE_MS = 5 * 60_000;

/** Dead-relay bound for the no-demand regime. Deliberately the SAME bound as a
 * subscribed relay: the writer heartbeats every 60s whether or not anyone is
 * subscribed, so an idle relay needs no extra slack to prove it is alive, and
 * two other readers of this same row (serviceHealthWindows.ts "ib-realtime-relay"
 * and scripts/watchdog/services.py) hold it to 5 minutes. Named separately only
 * so the two regimes read as the two distinct assertions they are. */
export const RELAY_IDLE_HEARTBEAT_STALE_MS = RELAY_HEARTBEAT_STALE_MS;

/** Quiet-book note only. A last fill older than this sets
 * journal_last_fill.warning and does not change checks.journal.fresh.
 * This book goes days without a fill (Fri close through the next session),
 * so a calendar bound on MAX(written_at) pages a live pipeline. */
export const JOURNAL_LAST_FILL_QUIET_MS = 3 * 24 * 3_600_000;

export const JOURNAL_LAST_FILL_WARNING =
  "last fill is older than 3 days; journal freshness follows pipeline liveness";

export const JOURNAL_GAP_SELF_ERROR_WARNING =
  "journal-gap-sli reported an error; journal freshness follows journal-sync";

export const JOURNAL_GAP_STALE_WARNING =
  "journal-gap-sli heartbeat is stale; journal freshness follows journal-sync";

const NOT_APPLICABLE: ProbeCheck = { applicable: false, age_secs: null, fresh: null };

function ageSecsFrom(timestampMs: number | null, nowMs: number): number | null {
  if (timestampMs === null || !Number.isFinite(timestampMs)) return null;
  const ageMs = nowMs - timestampMs;
  if (ageMs < -60_000) return null;
  return Math.max(0, Math.round(ageMs / 1000));
}

function parseRelayDetail(lastError: string | null): Record<string, unknown> {
  if (!lastError) return {};
  try {
    const parsed: unknown = JSON.parse(lastError);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/**
 * Writer liveness, including the RTH-only open-bell grace in {@link isStale}:
 * a last write from before today's 09:30 ET is compared to seconds-since-open,
 * not wall-clock age. Mid-session silence still uses the 5-minute bound.
 */
function heartbeatLive(row: RelayHealthRow, market: MarketState, nowMs: number): boolean {
  return !isStale("ib-realtime-relay", row.updated_at, market, nowMs);
}

/**
 * relay_tick — applicable during RTH only. The row carries two independent
 * signals and they are asserted separately:
 *
 *   writer liveness — how recently the relay refreshed the row (updated_at);
 *   tick flow       — how recently a tick arrived (detail.last_tick_at).
 *
 * Zero active subscriptions is NOT a failure: no demand means no reqMktData is
 * outstanding, so no tick can arrive and tick age asserts nothing. That
 * exemption is checked FIRST, because zero subscribers is precisely the regime
 * in which the relay has the least to say — reading it through the tick-flow
 * guard turned a healthy idle relay into a fake incident (2026-08-10). It is
 * still bounded by RELAY_IDLE_HEARTBEAT_STALE_MS, so a relay process that has
 * genuinely died with no subscribers is still caught.
 *
 * Overnight silence is also not a failure: the writer is RTH-only, so a
 * 16:00 ET close write probed at 09:30 ET must use the same seconds-since-open
 * rule as isStale (ib-realtime-relay is RTH_ONLY).
 */
export function evaluateRelayTick(
  row: RelayHealthRow | null,
  market: MarketState,
  nowMs: number,
): ProbeCheck {
  if (market !== "open") return NOT_APPLICABLE;
  if (!row) return { applicable: true, age_secs: null, fresh: false };

  const detail = parseRelayDetail(row.last_error);
  const lastTickAt = typeof detail.last_tick_at === "string" ? Date.parse(detail.last_tick_at) : NaN;
  const age_secs = ageSecsFrom(Number.isNaN(lastTickAt) ? null : lastTickAt, nowMs);

  if (row.state === "error") return { applicable: true, age_secs, fresh: false };

  // The idle exemption requires zero DEMAND, not just zero active tickets:
  // when IB nulls every subscription (error 200/354) the symbols stay
  // subscribed (detail.subscribed_symbols > 0) and silence is a blackout, so
  // the row falls through to the tick-flow guard below (R-061). Rows written
  // before subscribed_symbols existed carry no demand signal and keep the
  // idle reading.
  const subscribedSymbols =
    typeof detail.subscribed_symbols === "number" ? detail.subscribed_symbols : 0;
  if (detail.active_subscriptions === 0 && subscribedSymbols === 0) {
    return {
      applicable: true,
      age_secs,
      fresh: heartbeatLive(row, market, nowMs),
    };
  }

  if (!heartbeatLive(row, market, nowMs)) return { applicable: true, age_secs, fresh: false };

  return {
    applicable: true,
    age_secs,
    fresh: age_secs !== null && age_secs <= RELAY_TICK_FRESH_SECS,
  };
}

/**
 * Scan snapshot freshness per the writer's own cadence window. Scheduled
 * scans (vcg-scan, gex-scan) run an autonomous RTH cadence, so they are
 * asserted during "open" against their open window. On-demand writers
 * only run when a user acts — quiet is expected at ANY hour, so
 * they are never applicable. Flips automatically if the category in
 * SERVICE_FRESHNESS_WINDOWS ever changes.
 */
export function evaluateScanCheck(
  service: "vcg-scan" | "gex-scan",
  scanTime: string | null,
  market: MarketState,
  nowMs: number,
): ProbeCheck {
  if (market !== "open") return NOT_APPLICABLE;
  if (getServiceCategory(service) === "on-demand") return NOT_APPLICABLE;

  const parsed = parseScanTime(scanTime);
  const age_secs = ageSecsFrom(parsed ? parsed.getTime() : null, nowMs);
  const windowMs = getFreshnessWindowMs(service, "open");
  return {
    applicable: true,
    age_secs,
    fresh: age_secs !== null && age_secs * 1000 <= windowMs,
  };
}

function lastFillAgeSecs(writtenAt: string | null, nowMs: number): number | null {
  const parsed = parseScanTime(writtenAt);
  return ageSecsFrom(parsed ? parsed.getTime() : null, nowMs);
}

function missingExecCount(lastError: string | null): number | null {
  if (!lastError) return null;
  try {
    const parsed: unknown = JSON.parse(lastError);
    if (!parsed || typeof parsed !== "object") return null;
    const count = (parsed as Record<string, unknown>).missing_exec_id_count;
    return typeof count === "number" && Number.isFinite(count) ? count : null;
  } catch {
    return null;
  }
}

/** journal-sync heartbeats service_health every cycle (300s), including a
 * cycle that imports nothing. One error cycle is not a stall: get_fills
 * returns the whole day, so the next ok recovers it. Stale only when the
 * last ok is outside the 10-minute open window (isStale, including the
 * open-bell grace). While state is ok, updated_at is that ok. While it is
 * not, notOkSince is the events-row that opened the streak, which is the
 * moment it stopped being ok. */
function journalSyncLive(row: JournalHealthRow | null, nowMs: number): boolean {
  if (!row) return false;
  if (row.state === "ok") return !isStale("journal-sync", row.updated_at, "open", nowMs);
  if (!row.notOkSince) return false;
  return !isStale("journal-sync", row.notOkSince, "open", nowMs);
}

/** A fresh gap-sli row with missing_exec_id_count>0 pages. A missing row
 * pages. A self-error (state not ok, or no numeric count) and a stale
 * heartbeat do not: they are warnings, and the leg still follows journal-sync. */
function gapSliPages(row: JournalHealthRow | null, nowMs: number): boolean {
  if (!row) return true;
  if (isStale("journal-gap-sli", row.updated_at, "open", nowMs)) return false;
  const count = missingExecCount(row.last_error);
  return count !== null && count > 0;
}

export function journalGapWarning(row: JournalHealthRow | null, nowMs: number): string | null {
  if (!row) return null;
  if (isStale("journal-gap-sli", row.updated_at, "open", nowMs)) return JOURNAL_GAP_STALE_WARNING;
  const count = missingExecCount(row.last_error);
  if (count !== null && count > 0) return null;
  if (row.state !== "ok" || count !== 0) return JOURNAL_GAP_SELF_ERROR_WARNING;
  return null;
}

export function journalLastFill(writtenAt: string | null, nowMs: number): JournalLastFill {
  const age_secs = lastFillAgeSecs(writtenAt, nowMs);
  const quiet = age_secs !== null && age_secs * 1000 > JOURNAL_LAST_FILL_QUIET_MS;
  return { age_secs, warning: quiet ? JOURNAL_LAST_FILL_WARNING : null };
}

/**
 * journal — applicable during RTH only. fresh is journal-sync still ok
 * inside its open window AND no fresh gap-sli row reporting
 * missing_exec_id_count>0. A missing row fails the leg. A gap-sli
 * self-error or a stale gap-sli heartbeat does not. age_secs stays the
 * last-fill age and does not page. Outside RTH the leg is inapplicable.
 */
export function evaluateJournalCheck(
  input: JournalCheckInput,
  market: MarketState,
  nowMs: number,
): ProbeCheck {
  if (market !== "open") return NOT_APPLICABLE;
  return {
    applicable: true,
    age_secs: lastFillAgeSecs(input.writtenAt, nowMs),
    fresh: journalSyncLive(input.journalSync, nowMs) && !gapSliPages(input.journalGapSli, nowMs),
  };
}

export function buildFreshnessPayload(
  inputs: ProbeFreshnessInputs,
  now: Date = new Date(),
): ProbeFreshnessPayload {
  const market = getMarketStateFromDate(now);
  const nowMs = now.getTime();
  const checks = {
    relay_tick: evaluateRelayTick(inputs.relayRow, market, nowMs),
    vcg_scan: evaluateScanCheck("vcg-scan", inputs.vcgScanTime, market, nowMs),
    gex_scan: evaluateScanCheck("gex-scan", inputs.gexScanTime, market, nowMs),
    journal: evaluateJournalCheck(
      {
        writtenAt: inputs.journalWrittenAt,
        journalSync: inputs.journalSync,
        journalGapSli: inputs.journalGapSli,
      },
      market,
      nowMs,
    ),
  };
  const applicable = Object.values(checks).filter((check) => check.applicable);
  const database_failures = [...(inputs.databaseFailures ?? [])];
  const all_fresh =
    applicable.length === 0 ? null : applicable.every((check) => check.fresh === true);
  return {
    generated_at: now.toISOString(),
    market_state: market,
    checks,
    all_fresh,
    database_ok: database_failures.length === 0,
    database_failures,
    journal_last_fill: journalLastFill(inputs.journalWrittenAt, nowMs),
    journal_gap_warning: journalGapWarning(inputs.journalGapSli, nowMs),
  };
}
