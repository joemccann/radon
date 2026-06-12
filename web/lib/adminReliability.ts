/**
 * Pure reliability-summary helpers for the operator admin panel's Reliability
 * Strip (Section B) and metrics sections. Every value here is an HONEST
 * signal: the instantaneous tiles are computed from data that already exists
 * (systemd units, service_health rows, IB auth_state, the off-box
 * external_probe row), and the time-series tiles (uptime %, MTTR,
 * transitions, deploy markers) are computed from the append-only
 * service_health_events history table (migration 0011, DUR-11) — never
 * fabricated. Where history coverage is missing the value is null and the
 * UI says so.
 *
 * Dependency-free + DOM-free so it is unit-testable.
 */

import { authStateLabel, authStateTone, unitVerdict, type AuthStateTone } from "./adminFormat";
import {
  getMarketStateFromDate,
  isStale,
  type MarketState,
} from "./serviceHealthWindows";
import type {
  AdminHealthPayload,
  ExternalProbeRow,
  ServiceHealthRow,
  UnitStatus,
} from "./adminTypes";

// Tile 1 — Liveness: controllable units that are healthy right now.
export type LivenessSummary = { ok: number; total: number };

export function livenessSummary(units: UnitStatus[]): LivenessSummary {
  const controllable = units.filter((u) => u.can_control);
  const ok = controllable.filter((u) => unitVerdict(u).tone === "positive").length;
  return { ok, total: controllable.length };
}

// Tile 2 — Freshness: service_health rows past their per-writer staleness
// window. Market-state-aware via serviceHealthWindows (overnight quiet for a
// market-hours-only writer is NOT stale).
export type FreshnessSummary = { stale: number; total: number; staleServices: string[] };

export function freshnessSummary(
  rows: ServiceHealthRow[],
  market: MarketState = getMarketStateFromDate(),
  nowMs: number = Date.now(),
): FreshnessSummary {
  const staleServices = rows
    .filter((r) => isStale(r.service, r.updated_at ?? null, market, nowMs))
    .map((r) => r.service);
  return { stale: staleServices.length, total: rows.length, staleServices };
}

// Tile 3 — IB auth, with the documented stuck-pool detector folded in.
export type IbAuthSummary = { tone: AuthStateTone; label: string; poolStuck: boolean };

export function ibAuthSummary(health: AdminHealthPayload | null | undefined): IbAuthSummary {
  if (!health || !health.ib_gateway) return { tone: "neutral", label: "Unknown", poolStuck: false };
  const authState = health.ib_gateway.auth_state;
  const roles = Object.values(health.ib_pool ?? {});
  // feedback_ib_pool_stuck_after_2fa: authenticated but every pool client
  // disconnected is the documented stuck-pool symptom; a radon-api restart clears it.
  const poolStuck =
    authState === "authenticated" && roles.length > 0 && roles.every((r) => !r.connected);
  if (poolStuck) return { tone: "warning", label: "Auth OK, pool stuck", poolStuck: true };
  return { tone: authStateTone(authState), label: authStateLabel(authState), poolStuck: false };
}

// Tile 4 — Off-box probe (Tier-3). One sample, explicitly labelled "last probe
// latency", NEVER a percentile. Dead-man's-switch on checked_at.
export type ExternalProbeState = "healthy" | "down" | "stale" | "unknown";
export type ExternalProbeSummary = { state: ExternalProbeState; latencyMs: number | null };

export const EXTERNAL_PROBE_STALE_AFTER_SECONDS = 1200; // 20 min — see scripts/health_probe/reader.py

export function externalProbeSummary(
  probe: ExternalProbeRow | null | undefined,
  nowMs: number = Date.now(),
): ExternalProbeSummary {
  if (!probe || !probe.checked_at) return { state: "unknown", latencyMs: null };
  const ts = Date.parse(probe.checked_at);
  const latencyMs = typeof probe.latency_ms === "number" ? probe.latency_ms : null;
  if (Number.isNaN(ts)) return { state: "unknown", latencyMs };
  if (nowMs - ts > EXTERNAL_PROBE_STALE_AFTER_SECONDS * 1000) return { state: "stale", latencyMs };
  return { state: probe.ok === 1 ? "healthy" : "down", latencyMs };
}

// --- Time-series tiles (service_health_events history, DUR-11) -------------

/** One row of the append-only `service_health_events` table (migration 0011). */
export type ServiceHealthEventRow = {
  id?: number;
  service: string;
  state: string;
  detail?: string | null;
  created_at: string; // UTC ISO — the transition's service_health.updated_at
};

/** Deploy markers are upserted under this reserved service name by deploy.sh. */
export const DEPLOY_SERVICE = "deploy";

/** Default scoring window for the history tiles. */
export const RELIABILITY_WINDOW_MS = 7 * 24 * 3_600_000;

/** Shape served by GET /api/admin/reliability. `missing` means the history
 * table hasn't been migrated yet (200 + flag, never a 4xx). */
export type ReliabilityHistoryPayload = {
  window_ms: number;
  since: string;
  events: ServiceHealthEventRow[];
  baseline: Record<string, string>;
  missing?: boolean;
};

export type HistoryOptions = {
  windowStartMs: number;
  windowEndMs: number;
  /** State each service was in AT windowStart (its last event before the
   * window). Without it, scoring starts at the first in-window event. */
  baseline?: Record<string, string>;
};

export type ServiceHistory = {
  service: string;
  /** % of the observed span NOT in 'error'. Null when nothing was observed. */
  uptimePct: number | null;
  /** How much of the window could honestly be scored. */
  observedMs: number;
  /** History rows inside the window — each row is a recorded state change. */
  transitions: number;
  /** Entries INTO 'error' inside the window. */
  incidents: number;
  resolvedIncidents: number;
  /** Mean error -> recovery duration of resolved incidents; null when none. */
  mttrMs: number | null;
  /** State as of windowEnd. */
  currentState: string;
};

export type DeployMarker = { sha: string | null; at: string };

const ERROR_STATE = "error";

type TimedEvent = { atMs: number; state: string };

function inWindowEventsByService(
  events: ServiceHealthEventRow[],
  { windowStartMs, windowEndMs }: HistoryOptions,
): Map<string, TimedEvent[]> {
  const byService = new Map<string, TimedEvent[]>();
  for (const event of events) {
    if (event.service === DEPLOY_SERVICE) continue;
    const atMs = Date.parse(event.created_at);
    if (Number.isNaN(atMs) || atMs < windowStartMs || atMs > windowEndMs) continue;
    const list = byService.get(event.service) ?? [];
    list.push({ atMs, state: event.state });
    byService.set(event.service, list);
  }
  for (const list of byService.values()) list.sort((a, b) => a.atMs - b.atMs);
  return byService;
}

function scoreService(
  service: string,
  timeline: TimedEvent[],
  baselineState: string | undefined,
  { windowStartMs, windowEndMs }: HistoryOptions,
): ServiceHistory | null {
  const segments: TimedEvent[] = baselineState
    ? [{ atMs: windowStartMs, state: baselineState }, ...timeline]
    : timeline;
  if (segments.length === 0) return null;

  const observedStartMs = segments[0].atMs;
  const observedMs = Math.max(0, windowEndMs - observedStartMs);

  let errorMs = 0;
  let incidents = 0;
  const repairsMs: number[] = [];
  let openErrorSinceMs: number | null = null;

  segments.forEach((segment, i) => {
    const nextAtMs = i + 1 < segments.length ? segments[i + 1].atMs : windowEndMs;
    if (segment.state === ERROR_STATE) {
      errorMs += Math.max(0, nextAtMs - segment.atMs);
      if (openErrorSinceMs === null) {
        openErrorSinceMs = segment.atMs;
        // A baseline already in error entered before the window: ongoing, not new.
        const enteredInsideWindow = !(baselineState === ERROR_STATE && i === 0);
        if (enteredInsideWindow) incidents += 1;
      }
    } else if (openErrorSinceMs !== null) {
      repairsMs.push(segment.atMs - openErrorSinceMs);
      openErrorSinceMs = null;
    }
  });

  const uptimePct = observedMs > 0 ? (1 - errorMs / observedMs) * 100 : null;
  const mttrMs =
    repairsMs.length > 0 ? repairsMs.reduce((a, b) => a + b, 0) / repairsMs.length : null;

  return {
    service,
    uptimePct,
    observedMs,
    transitions: timeline.length,
    incidents,
    resolvedIncidents: repairsMs.length,
    mttrMs,
    currentState: segments[segments.length - 1].state,
  };
}

/**
 * Per-service uptime / MTTR / transition history over the window. Services
 * appear when they have in-window events OR a baseline state; deploy markers
 * are excluded (see {@link deployMarkers}).
 */
export function serviceHistorySummaries(
  events: ServiceHealthEventRow[],
  opts: HistoryOptions,
): ServiceHistory[] {
  const byService = inWindowEventsByService(events, opts);
  const services = new Set<string>([
    ...byService.keys(),
    ...Object.keys(opts.baseline ?? {}),
  ]);
  const summaries: ServiceHistory[] = [];
  for (const service of [...services].sort()) {
    const summary = scoreService(
      service,
      byService.get(service) ?? [],
      opts.baseline?.[service],
      opts,
    );
    if (summary) summaries.push(summary);
  }
  return summaries;
}

/** Deploy markers newest-first; `sha` comes from the row's detail column. */
export function deployMarkers(events: ServiceHealthEventRow[]): DeployMarker[] {
  return events
    .filter((event) => event.service === DEPLOY_SERVICE)
    .map((event) => ({ sha: event.detail ?? null, at: event.created_at }))
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}

export type HistoryRollup = {
  worstUptime: { service: string; uptimePct: number } | null;
  totalTransitions: number;
  topFlapper: { service: string; transitions: number } | null;
  incidents: number;
  resolvedIncidents: number;
  /** Mean repair time across every resolved incident in the window. */
  mttrMs: number | null;
  deploys: DeployMarker[];
};

/** Strip-level aggregate of {@link serviceHistorySummaries} + deploy markers. */
export function reliabilityRollup(
  events: ServiceHealthEventRow[],
  opts: HistoryOptions,
): HistoryRollup {
  const summaries = serviceHistorySummaries(events, opts);

  let worstUptime: HistoryRollup["worstUptime"] = null;
  let topFlapper: HistoryRollup["topFlapper"] = null;
  let totalTransitions = 0;
  let incidents = 0;
  let resolvedIncidents = 0;
  let weightedRepairMs = 0;

  for (const s of summaries) {
    totalTransitions += s.transitions;
    incidents += s.incidents;
    resolvedIncidents += s.resolvedIncidents;
    if (s.mttrMs !== null) weightedRepairMs += s.mttrMs * s.resolvedIncidents;
    if (s.uptimePct !== null && (worstUptime === null || s.uptimePct < worstUptime.uptimePct)) {
      worstUptime = { service: s.service, uptimePct: s.uptimePct };
    }
    if (s.transitions > 0 && (topFlapper === null || s.transitions > topFlapper.transitions)) {
      topFlapper = { service: s.service, transitions: s.transitions };
    }
  }

  return {
    worstUptime,
    totalTransitions,
    topFlapper,
    incidents,
    resolvedIncidents,
    mttrMs: resolvedIncidents > 0 ? weightedRepairMs / resolvedIncidents : null,
    deploys: deployMarkers(events),
  };
}
