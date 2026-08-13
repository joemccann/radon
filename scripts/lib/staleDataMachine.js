/**
 * Pure decision core for the WS relay's stale-tick recovery ladder.
 *
 * The relay's data plane can go silent while the IB socket stays alive
 * (Gateway data farms drop without tearing down the TCP connection). The
 * relay wires its real timers, IB calls, and service_health writes to this
 * module; everything here is a pure function of an immutable input snapshot
 * — no IB, no timers, no network, no DB. That makes the ladder + escalation
 * policy unit-testable in isolation and keeps the relay's I/O at the edges.
 *
 * Actions returned by decideStaleAction:
 *
 *   - "none"        — healthy, off-hours, no subscriptions, or ticks fresh.
 *   - "resubscribe" — ticks stale but a farm-OK signal arrived; the socket
 *                     is up, so re-issue reqMktData rather than bounce it.
 *   - "reconnect"   — ticks stale, no farm-OK hint; bounce the IB socket
 *                     (disconnect + scheduled reconnect). Bounded by K.
 *   - "escalate"    — K consecutive reconnect cycles failed to restore
 *                     ticks during RTH; alert via service_health (and let
 *                     the relay hand off to the 2FA-locked restart path).
 *
 * Escalation is rate-limited by ESCALATION_COOLDOWN_MS so a persistently
 * dead farm raises one alert per cooldown window, never a stacked storm.
 */

/** No tick for this long during RTH is considered stale. */
export const STALE_DATA_THRESHOLD_MS = 45_000;

/** How often the relay re-evaluates staleness. */
export const STALE_CHECK_INTERVAL_MS = 30_000;

/**
 * Consecutive failed reconnect cycles (still stale during RTH) tolerated
 * before escalating to an alert. K=3 with the 45s threshold avoids churn
 * during a normal connect/warm-up while still surfacing a real outage
 * inside a couple of minutes.
 */
export const MAX_RECONNECT_CYCLES = 3;

/** Minimum gap between escalation alerts so we never stack pushes. */
export const ESCALATION_COOLDOWN_MS = 900_000; // 15 min

/**
 * IB info codes that report market-data farm connection state. When the
 * most recent farm signal is a positive "connection is OK" (2104 / 2106 /
 * 2158), the socket is healthy and the right recovery is a fresh
 * resubscribe rather than a socket bounce.
 */
export const FARM_OK_CODES = new Set([2104, 2106, 2158]);

/**
 * IB info codes that report a market-data farm as broken / inactive
 * (2103 connection lost, 2105 HMDS lost, 2108 connection inactive). These
 * mean a socket bounce is the appropriate recovery.
 */
export const FARM_DOWN_CODES = new Set([2103, 2105, 2108]);

/**
 * @typedef {Object} StaleDataInput
 * @property {number} now                       Current epoch ms.
 * @property {number} lastTickAt                Epoch ms of the last tick.
 * @property {boolean} ibConnected              IB socket currently up.
 * @property {boolean} isMarketHours            Inside RTH (relay's gate).
 * @property {number} activeSubscriptions       Count of live L1 subjects.
 * @property {number} reconnectCycles           Consecutive failed reconnect
 *                                              cycles so far this episode.
 * @property {number|null} farmState            Last IB farm info code, or null.
 * @property {number|null} lastEscalationAt      Epoch ms of the last escalate,
 *                                              or null if never escalated.
 */

/**
 * Decide the next recovery action from an immutable snapshot. Pure: no
 * side effects, no clock reads — ``now`` is supplied by the caller.
 *
 * @param {StaleDataInput} input
 * @returns {"none" | "resubscribe" | "reconnect" | "escalate"}
 */
export function decideStaleAction(input) {
  const {
    now,
    lastTickAt,
    ibConnected,
    isMarketHours,
    activeSubscriptions,
    reconnectCycles,
    farmState,
    lastEscalationAt,
  } = input;

  // Guard clauses: nothing to recover when off-hours, disconnected, idle,
  // or ticks are still fresh. Off-hours quiet is normal — never alert.
  if (!isMarketHours) return "none";
  if (!ibConnected) return "none";
  if (activeSubscriptions <= 0) return "none";
  if (now - lastTickAt <= STALE_DATA_THRESHOLD_MS) return "none";

  // Ticks are stale during RTH with live subscriptions.

  // A positive farm signal means the socket is healthy — prefer a fresh
  // resubscribe (cheap, no auth churn) over bouncing the connection.
  if (farmState != null && FARM_OK_CODES.has(farmState)) {
    return "resubscribe";
  }

  // Bounded ladder: bounce the socket up to K times. Once we've burned K
  // reconnect cycles and ticks are still stale, escalate to an alert.
  if (reconnectCycles < MAX_RECONNECT_CYCLES) {
    return "reconnect";
  }

  // Escalation is rate-limited so a persistently dead farm raises one
  // alert per cooldown window, never a stacked push storm.
  if (lastEscalationAt != null && now - lastEscalationAt < ESCALATION_COOLDOWN_MS) {
    return "none";
  }

  return "escalate";
}

/**
 * True iff ``code`` is a known market-data farm info code (OK or down).
 * The relay uses this to decide whether an EventName.info payload should
 * update farmState fed into decideStaleAction.
 *
 * @param {number} code
 * @returns {boolean}
 */
export function isFarmStateCode(code) {
  return FARM_OK_CODES.has(code) || FARM_DOWN_CODES.has(code);
}

/** Cadence of the relay's RTH tick heartbeat (DUR-16). */
export const TICK_HEARTBEAT_INTERVAL_MS = 60_000;

export function summarizeSubscriptionFreshness(subjects, now) {
  const active = subjects.filter((subject) => subject?.active === true);
  if (active.length === 0) {
    return { activeSubscriptions: 0, lastTickAt: now };
  }
  const timestamps = active.map((subject) => Number(subject.lastTickAt));
  const oldest = timestamps.every(Number.isFinite) ? Math.min(...timestamps) : 0;
  return { activeSubscriptions: active.length, lastTickAt: oldest };
}

/**
 * DUR-16: should the relay refresh its service_health row with the
 * current last-tick timestamp this cycle?
 *
 * The heartbeat exists so GET /api/probe/freshness can compute true tick
 * age from the row's detail JSON (last_tick_at) instead of guessing from
 * sparse event-driven writes. It runs ONLY during RTH (off-hours the relay
 * stays event-driven, matching its serviceHealthWindows entry) and never
 * while the error row is latched — escalation/recovery edges stay owned by
 * the ladder. ok->ok upserts are suppressed by the migration-0011 events
 * trigger, so the heartbeat adds zero history rows.
 *
 * @param {{now: number, isMarketHours: boolean, inError: boolean, lastHeartbeatAt: number}} input
 * @returns {boolean}
 */
export function shouldWriteTickHeartbeat({ now, isMarketHours, inError, lastHeartbeatAt }) {
  if (!isMarketHours) return false;
  if (inError) return false;
  return now - lastHeartbeatAt >= TICK_HEARTBEAT_INTERVAL_MS;
}

/**
 * True when the relay's data plane is in a state worth reporting as ok.
 *
 * A fresh tick is direct proof the data plane works, whatever the last farm
 * info code said (2103 is emitted on routine farm resets and ``farmState`` is
 * sticky), so it settles the question in either regime.
 *
 * Without a fresh tick the two regimes diverge, and the distinction is the
 * whole point. With live subscriptions the relay owes ticks, so silence is an
 * unhealthy data plane. With ZERO subscriptions no reqMktData is outstanding,
 * so no tick can ever arrive and tick age asserts nothing about the relay —
 * folding tick age into that regime is what silenced the heartbeat for the
 * whole idle window and froze the row (the 2026-08-10 stale-freshness
 * incident).
 *
 * Idle is NOT blind on the IB socket: a disconnected relay has nothing
 * healthy to report. A leftover farm-DOWN code is different — 2103/2105/2108
 * stick on lastFarmStateCode across drain and overnight, so treating them as
 * "currently DOWN" while demand is already zero silences the idle heartbeat
 * and freezes the row (the same 2026-08-10 fingerprint). Farm-down recovery
 * is the subscribed ladder's job.
 *
 * @param {StaleDataInput} input
 * @returns {boolean}
 */
function hasHealthyDataPlane({ ibConnected, activeSubscriptions, now, lastTickAt }) {
  if (!ibConnected) return false;
  if (now - lastTickAt <= STALE_DATA_THRESHOLD_MS) return true;
  if (activeSubscriptions > 0) return false;
  return true;
}

/**
 * Next lastFarmStateCode after an IB farm info event.
 * Farm-OK while idle clears leftover DOWN so it cannot ride into the next
 * idle heartbeat cycle. Farm-OK while subscribed is kept so
 * {@link decideStaleAction} can prefer resubscribe.
 *
 * @param {number|null} current
 * @param {number} code
 * @param {number} [activeSubscriptions=0]
 * @returns {number|null}
 */
export function nextFarmStateCode(current, code, activeSubscriptions = 0) {
  if (!isFarmStateCode(code)) return current;
  if (FARM_OK_CODES.has(code) && activeSubscriptions <= 0) return null;
  return code;
}

/** Last client gone: leftover farm codes are no longer current. */
export function farmStateAfterIdleDrain() {
  return null;
}

/**
 * Decide BOTH the recovery action and whether to write the ok tick-heartbeat
 * from a single snapshot, so the two can never race.
 *
 * The relay fires every service_health write void (fire-and-forget) into a
 * last-write-wins upsert. If an "ok" heartbeat and an "error" escalation are
 * emitted in the SAME stale-check cycle, the heartbeat can land last and
 * clobber the escalation's error row — hiding a dead data plane behind a green
 * row whose own payload says ``tick_age_secs: 195`` (the 2026-06-18 incident).
 *
 * The fix: the ladder owns the health row whenever it is acting. Only a fully
 * healthy cycle (action ``none``) with a healthy data plane may write the
 * heartbeat. Off-hours and latched-error suppression stay in
 * {@link shouldWriteTickHeartbeat} (the latter still matters during the
 * post-escalation cooldown, when decideStaleAction returns ``none`` but the
 * error row is still latched).
 *
 * The row reports THIS WRITER's state, not the tick stream's
 * (feedback_service_health_writer_state_not_event_content), so the idle path is
 * a heartbeat path, not a skip path — the payload stays honest (real
 * active_subscriptions, real tick_age_secs) and readers get writer liveness.
 *
 * ``clearError`` is the latch's second recovery edge. The first — a tick
 * arriving — is unreachable in exactly the regime that strands it: an
 * escalation during RTH, then the last browser client is reaped, subscriptions
 * drain to zero, and no reqMktData remains to ever produce a tick. The row then
 * sits latched at "error" for hours against a relay that is merely idle. So the
 * latch also clears on the writer-state edge: the ladder has nothing to act on
 * and the data plane is healthy, which means the escalation's own precondition
 * (stale ticks with demand outstanding) is gone. A leftover farm-DOWN code
 * does not hold the latch once demand is gone.
 *
 * @param {StaleDataInput & {inError: boolean, lastHeartbeatAt: number}} input
 * @returns {{action: "none"|"resubscribe"|"reconnect"|"escalate", heartbeat: boolean, clearError: boolean}}
 */
export function decideHealthWrite(input) {
  const action = decideStaleAction(input);
  const isHealthyCycle = action === "none" && hasHealthyDataPlane(input);
  const clearError = isHealthyCycle && input.inError === true;
  const heartbeat = isHealthyCycle && shouldWriteTickHeartbeat(input);
  return { action, heartbeat, clearError };
}

/**
 * Gateway modes from which the relay may drive a Gateway restart on escalation.
 *
 * docker/cloud: the Gateway runs as a container the radon stack owns; the relay
 * hands off to the lock-holding ``POST /ib/restart`` (which owns the 2FA push
 * lock + backoff) so a dead market-data farm self-heals. launchd/local: the
 * Gateway is the operator's laptop IBC session — repeated relay-driven restarts
 * thrash the auth session and stack 2FA prompts, so the relay stays alert-only.
 *
 * @param {string|undefined} gatewayMode
 * @returns {boolean}
 */
export function shouldRequestGatewayRestart(gatewayMode) {
  return gatewayMode === "docker" || gatewayMode === "cloud";
}
