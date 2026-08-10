/**
 * Pure evaluation core for GET /api/probe/freshness (DUR-16).
 *
 * The Tier-3 off-box prober calls the route every few minutes and records
 * tick/scan/journal freshness into external_probe_runs. The contract shape
 * is FIXED (the prober is built against it):
 *
 *   { generated_at, market_state, database_ok, database_failures,
 *     checks: { relay_tick, vcg_scan, gex_scan, journal },
 *     all_fresh }
 *
 * Each check is { applicable, age_secs, fresh }. applicable=false (and
 * fresh=null) when the market-state windows say the writer is expected
 * quiet — NEVER a uniform staleness model (feedback_service_health_staleness,
 * feedback_extended_market_state_window). all_fresh ANDs the applicable
 * checks and is null when none apply.
 *
 * No DB, no clock, no fetch in this module — the route supplies raw inputs
 * and `now`, so the whole matrix is unit-testable with a pinned clock.
 */

import {
  getFreshnessWindowMs,
  getMarketStateFromDate,
  getServiceCategory,
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
  /** MAX(written_at) from journal. */
  journalWrittenAt: string | null;
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

/** Fills are only asserted loosely: the account trades most sessions, so a
 * journal silent past the weekend-covering window (Fri 16:00 ET -> Mon
 * 09:30 ET ~ 65h) during RTH signals a dead fill pipeline. Matches the
 * journal-sync closed window in serviceHealthWindows. */
export const JOURNAL_FRESH_WINDOW_MS = 3 * 24 * 3_600_000;

const NOT_APPLICABLE: ProbeCheck = { applicable: false, age_secs: null, fresh: null };

function ageSecsFrom(timestampMs: number | null, nowMs: number): number | null {
  if (timestampMs === null || Number.isNaN(timestampMs)) return null;
  return Math.max(0, Math.round((nowMs - timestampMs) / 1000));
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

function heartbeatAgeMsFrom(row: RelayHealthRow, nowMs: number): number | null {
  const heartbeatAt = row.updated_at ? Date.parse(row.updated_at) : NaN;
  return Number.isNaN(heartbeatAt) ? null : nowMs - heartbeatAt;
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

  const heartbeatAgeMs = heartbeatAgeMsFrom(row, nowMs);

  if (detail.active_subscriptions === 0) {
    return {
      applicable: true,
      age_secs,
      fresh: heartbeatAgeMs !== null && heartbeatAgeMs <= RELAY_IDLE_HEARTBEAT_STALE_MS,
    };
  }

  const heartbeatLive = heartbeatAgeMs !== null && heartbeatAgeMs <= RELAY_HEARTBEAT_STALE_MS;
  if (!heartbeatLive) return { applicable: true, age_secs, fresh: false };

  return {
    applicable: true,
    age_secs,
    fresh: age_secs !== null && age_secs <= RELAY_TICK_FRESH_SECS,
  };
}

/**
 * Scan snapshot freshness per the writer's own cadence window. Scheduled
 * scans (vcg-scan) run an autonomous RTH cadence, so they are asserted
 * during "open" against their open window. On-demand writers (gex-scan
 * today) only run when a user acts — quiet is expected at ANY hour, so
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

/**
 * journal — fills are only expected while the market trades, so the check
 * applies during "open" and asserts the newest row is inside the
 * weekend-covering window (a hard intraday window would false-alarm on any
 * quiet trading morning).
 */
export function evaluateJournalCheck(
  writtenAt: string | null,
  market: MarketState,
  nowMs: number,
): ProbeCheck {
  if (market !== "open") return NOT_APPLICABLE;
  const parsed = parseScanTime(writtenAt);
  const age_secs = ageSecsFrom(parsed ? parsed.getTime() : null, nowMs);
  return {
    applicable: true,
    age_secs,
    fresh: age_secs !== null && age_secs * 1000 <= JOURNAL_FRESH_WINDOW_MS,
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
    journal: evaluateJournalCheck(inputs.journalWrittenAt, market, nowMs),
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
  };
}
