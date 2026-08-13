/**
 * Regression: incident-20260810T194500Z-stale-market-data-freshness
 * (P2, fingerprint `stale-freshness:relay_tick`).
 *
 * During RTH the freshness probe reported `checks.relay_tick.fresh === false`
 * while the relay was demonstrably alive — journald showed only "Closing
 * unresponsive client (no pong for 90s)", zero `[stale-data]` ladder lines
 * fired all session, and `service_health_events` has had no
 * `ib-realtime-relay` row since 2026-07-10. Not a farm-down, not the
 * heartbeat-clobber mode: the relay simply stopped writing.
 *
 * Failure topology (writer -> row -> evaluator), all real code:
 *
 *   1. The last browser client is reaped, so `disconnectClient()` drains
 *      `symbolSubscribers` to 0 and every `reqMktData` is cancelled. No tick
 *      can then arrive — `markTick()` (ib_realtime_server.js) is the only
 *      thing that advances `lastTickTimestamp`, and only tick handlers call it.
 *   2. Every stale-check cycle the relay asks `decideHealthWrite`.
 *      `decideStaleAction` correctly returns "none" for zero subscribers, but
 *      HEAD ALSO gates the heartbeat on `hasFreshConnectedTick`
 *      (`ibConnected && now - lastTickAt <= STALE_DATA_THRESHOLD_MS`). With no
 *      subscribers, and therefore no ticks, that goes false forever — and the
 *      call site has no `else`, so the relay stops writing its row ENTIRELY.
 *   3. The row freezes. Its `updated_at` ages past RELAY_HEARTBEAT_STALE_MS.
 *   4. `evaluateRelayTick` fails at the `heartbeatLive` guard, which HEAD
 *      checks ABOVE the deliberate `active_subscriptions === 0 -> fresh`
 *      exemption — making that exemption unreachable in exactly the regime it
 *      was written for. -> relay_tick.fresh=false -> all_fresh=false -> P2.
 *   5. A client reconnects, ticks resume, the heartbeat resumes, the incident
 *      "resolves" — and flaps on the next disconnect.
 *
 * WHY THIS TEST EXISTS AT ALL. `probe-freshness.test.ts` already asserts the
 * zero-subscription branch — but with a HAND-WRITTEN row carrying `subs: 0`
 * AND a live `updated_at`, a row the writer provably never emits at zero
 * subscribers. `staleDataMachine.test.js` tests the writer in isolation. Each
 * side is correct alone; nobody composed them, so the seam between them was
 * the blind spot. Every row asserted below is therefore produced BY the real
 * `decideHealthWrite`, including its real "no write at all" outcome, and fed
 * to the real `evaluateRelayTick` / `buildFreshnessPayload`. Nothing about the
 * frozen row is hand-authored.
 */
import { describe, expect, it } from "vitest";

import {
  decideHealthWrite,
  STALE_CHECK_INTERVAL_MS,
  STALE_DATA_THRESHOLD_MS,
  TICK_HEARTBEAT_INTERVAL_MS,
} from "../../scripts/lib/staleDataMachine.js";
import { isMarketOpen } from "../../scripts/lib/marketCalendar.js";
import {
  buildFreshnessPayload,
  evaluateRelayTick,
  RELAY_HEARTBEAT_STALE_MS,
  RELAY_IDLE_HEARTBEAT_STALE_MS,
  type RelayHealthRow,
} from "../lib/probeFreshness";
import { getFreshnessWindowMs, getMarketStateFromDate } from "../lib/serviceHealthWindows";

/**
 * The live production instants, from the frozen row captured at detection:
 *   {"heartbeat":"tick","last_tick_at":"2026-08-10T19:37:32.258Z",
 *    "tick_age_secs":9,"active_subscriptions":0}
 * written at ~19:37:41Z, then 7m19s of silence until the probe fired.
 */
const PRODUCTION_LAST_WRITE_MS = Date.parse("2026-08-10T19:37:41Z");
const DETECTION_MS = Date.parse("2026-08-10T19:45:00Z");

/** Relay boot / first modelled stale-check, five minutes of live trading
 * before the disconnect so the seed row is one the WRITER produced. */
const SESSION_START_MS = PRODUCTION_LAST_WRITE_MS - 5 * 60_000;

/** A subscribed relay during the warm-up. Count is incidental. */
const WARM_SUBSCRIPTIONS = 12;

type Phase = {
  durationMs: number;
  activeSubscriptions: number;
  /** Do ticks keep arriving? False models an idle relay with no demand. */
  ticksFlowing: boolean;
};

type HealthWrite = {
  atMs: number;
  activeSubscriptions: number;
  tickAgeSecs: number;
};

type RelayRun = {
  /** Whatever row the writer last emitted — null if it never emitted one. */
  row: RelayHealthRow | null;
  writes: HealthWrite[];
  actions: string[];
};

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

/**
 * Replay of the relay's stale-check cycle (ib_realtime_server.js
 * `checkStaleData` -> `decideHealthWrite` -> `writeRelayHealth`) over a pinned
 * clock. The real decision function, the real row payload, and — critically —
 * the real consequence of a `false` heartbeat: no write at all, so the row
 * genuinely ages instead of being refreshed by the test harness.
 */
function runRelayStaleCheckLoop({
  startMs,
  phases,
  farmState = null,
}: {
  startMs: number;
  phases: Phase[];
  /** Leftover IB farm info code. Sticky across drain unless the writer ignores it. */
  farmState?: number | null;
}): RelayRun {
  let lastTickTimestamp = startMs;
  let lastHeartbeatAt = startMs;
  let row: RelayHealthRow | null = null;
  const writes: HealthWrite[] = [];
  const actions: string[] = [];
  let now = startMs;

  for (const phase of phases) {
    const phaseEnd = now + phase.durationMs;
    while (now < phaseEnd) {
      now += STALE_CHECK_INTERVAL_MS;
      if (phase.ticksFlowing) lastTickTimestamp = now;

      const { action, heartbeat } = decideHealthWrite({
        now,
        lastTickAt: lastTickTimestamp,
        ibConnected: true,
        isMarketHours: isMarketOpen(new Date(now)),
        activeSubscriptions: phase.activeSubscriptions,
        reconnectCycles: 0,
        farmState,
        lastEscalationAt: null,
        inError: false,
        lastHeartbeatAt,
      });

      if (action !== "none") actions.push(action);
      if (!heartbeat) continue;

      lastHeartbeatAt = now;
      const write: HealthWrite = {
        atMs: now,
        activeSubscriptions: phase.activeSubscriptions,
        tickAgeSecs: Math.round((now - lastTickTimestamp) / 1000),
      };
      writes.push(write);
      row = {
        state: "ok",
        last_error: JSON.stringify({
          heartbeat: "tick",
          last_tick_at: iso(lastTickTimestamp),
          tick_age_secs: write.tickAgeSecs,
          active_subscriptions: write.activeSubscriptions,
        }),
        updated_at: iso(now),
      };
    }
  }

  return { row, writes, actions };
}

/**
 * The relay's stale-check cadence (30s) and heartbeat cadence (60s) mean a
 * client disconnect lands in one of two phases relative to the last heartbeat.
 * Both are real; asserting only one would let a fix that merely shifted the
 * phase pass. `mid-cycle` is the phase production captured — the writer got
 * one last heartbeat out at zero subscribers with a still-fresh tick age,
 * which is exactly the row that then froze.
 */
const DISCONNECT_PHASES = [
  { label: "mid-cycle (production capture)", warmupMs: 5 * 60_000 - STALE_CHECK_INTERVAL_MS },
  { label: "on-cycle", warmupMs: 5 * 60_000 },
] as const;

function idleRunFor(warmupMs: number, startMs = SESSION_START_MS, endMs = DETECTION_MS) {
  const disconnectMs = startMs + warmupMs;
  const run = runRelayStaleCheckLoop({
    startMs,
    phases: [
      { durationMs: warmupMs, activeSubscriptions: WARM_SUBSCRIPTIONS, ticksFlowing: true },
      { durationMs: endMs - disconnectMs, activeSubscriptions: 0, ticksFlowing: false },
    ],
  });
  return { ...run, disconnectMs };
}

/**
 * Longest silence a reader of this row would observe across the idle window:
 * from the last write at-or-before the disconnect, through every later write,
 * to the probe instant.
 */
function maxObservedWriteGapMs(writes: HealthWrite[], disconnectMs: number, probeMs: number): number {
  const before = writes.filter((w) => w.atMs <= disconnectMs).map((w) => w.atMs).pop();
  const timeline = [
    ...(before === undefined ? [] : [before]),
    ...writes.filter((w) => w.atMs > disconnectMs).map((w) => w.atMs),
    probeMs,
  ];
  return Math.max(...timeline.map((at, i) => (i === 0 ? 0 : at - timeline[i - 1])));
}

function freshnessInputsFor(row: RelayHealthRow | null, nowMs: number) {
  const vcgWindowMs = getFreshnessWindowMs("vcg-scan", "open");
  return {
    relayRow: row,
    vcgScanTime: iso(nowMs - Math.floor(vcgWindowMs / 2)),
    gexScanTime: iso(nowMs - Math.floor(vcgWindowMs / 2)),
    journalWrittenAt: iso(nowMs - 3_600_000),
  };
}

describe("idle relay (zero subscribers) freshness — writer/evaluator seam", () => {
  it("pins the incident scenario to RTH on both the relay and probe clocks", () => {
    expect(isMarketOpen(new Date(SESSION_START_MS))).toBe(true);
    expect(isMarketOpen(new Date(DETECTION_MS))).toBe(true);
    expect(getMarketStateFromDate(new Date(DETECTION_MS))).toBe("open");
    expect(DETECTION_MS - PRODUCTION_LAST_WRITE_MS).toBeGreaterThan(RELAY_HEARTBEAT_STALE_MS);
  });

  it("reproduces the captured production row from the real writer, not by hand", () => {
    const { writes, disconnectMs } = idleRunFor(DISCONNECT_PHASES[0].warmupMs);
    const firstIdleWrite = writes.find((w) => w.atMs > disconnectMs);

    expect(
      firstIdleWrite,
      "the writer must get one last row out after the client is reaped — that is the row production froze on",
    ).toBeDefined();
    expect(
      firstIdleWrite?.activeSubscriptions,
      "the live capture froze at active_subscriptions=0 — the harness must enter that same regime",
    ).toBe(0);
    expect(
      (firstIdleWrite?.tickAgeSecs ?? Infinity) * 1000,
      "and with a still-FRESH tick age (production: 9s), proving the relay was alive when it went silent",
    ).toBeLessThanOrEqual(STALE_DATA_THRESHOLD_MS);
  });

  describe.each(DISCONNECT_PHASES)("client disconnect, $label", ({ warmupMs }) => {
    it("keeps writing service_health while it has no subscribers", () => {
      const { writes, actions, disconnectMs } = idleRunFor(warmupMs);
      const gapMs = maxObservedWriteGapMs(writes, disconnectMs, DETECTION_MS);
      const idleWrites = writes.filter((w) => w.atMs > disconnectMs);

      expect(actions, "an idle relay must not run the stale-tick recovery ladder").toEqual([]);
      expect(
        gapMs,
        `relay wrote service_health ${idleWrites.length} time(s) across ${DETECTION_MS - disconnectMs}ms of ` +
          `zero-subscriber RTH idle; longest observed silence ${gapMs}ms exceeds the probe's ` +
          `RELAY_HEARTBEAT_STALE_MS=${RELAY_HEARTBEAT_STALE_MS} dead-relay guard, so the row goes stale ` +
          `while the relay is alive (heartbeat cadence is ${TICK_HEARTBEAT_INTERVAL_MS}ms)`,
      ).toBeLessThanOrEqual(RELAY_HEARTBEAT_STALE_MS);
    });

    it("does not report relay_tick stale for a relay that is alive with zero subscribers", () => {
      const { row } = idleRunFor(warmupMs);
      const rowAgeMs = row?.updated_at ? DETECTION_MS - Date.parse(row.updated_at) : Infinity;
      const check = evaluateRelayTick(row, "open", DETECTION_MS);

      expect(check.applicable).toBe(true);
      expect(
        check.fresh,
        `relay row is ${rowAgeMs}ms old (guard ${RELAY_HEARTBEAT_STALE_MS}ms) because the writer suppressed ` +
          `the heartbeat once ticks stopped at zero subscribers, so evaluateRelayTick never reached its ` +
          `active_subscriptions===0 exemption`,
      ).not.toBe(false);
    });

    it("does not fire all_fresh=false through buildFreshnessPayload", () => {
      const { row } = idleRunFor(warmupMs);
      const payload = buildFreshnessPayload(freshnessInputsFor(row, DETECTION_MS), new Date(DETECTION_MS));

      expect(payload.market_state).toBe("open");
      // toBe(true), not .not.toBe(false): null satisfies the looser form, so a
      // "fix" that made relay_tick inapplicable during RTH would pass — and that
      // is a suppression, blinding the only detector for a truly dead relay.
      expect(payload.checks.relay_tick.applicable, "relay_tick must stay applicable during RTH").toBe(true);
      expect(payload.checks.relay_tick.fresh, "incident fingerprint stale-freshness:relay_tick").toBe(true);
      expect(payload.all_fresh, "probe raised all_fresh=false for a demonstrably alive relay").toBe(true);
    });
  });

  it("control: a subscribed relay with flowing ticks stays fresh through the same harness", () => {
    const run = runRelayStaleCheckLoop({
      startMs: SESSION_START_MS,
      phases: [
        {
          durationMs: DETECTION_MS - SESSION_START_MS,
          activeSubscriptions: WARM_SUBSCRIPTIONS,
          ticksFlowing: true,
        },
      ],
    });

    expect(run.writes.length).toBeGreaterThan(1);
    const payload = buildFreshnessPayload(freshnessInputsFor(run.row, DETECTION_MS), new Date(DETECTION_MS));
    expect(payload.checks.relay_tick.fresh).toBe(true);
    expect(payload.all_fresh).toBe(true);
  });
});

/**
 * The other half of the contract. relay_tick is the ONLY detector for a relay
 * process that has genuinely died, so the zero-subscriber exemption has to be a
 * BOUNDED exemption, not a blanket amnesty: a row that stopped moving because
 * nobody is writing it must still read stale. The fix must not buy idle-relay
 * calm by blinding the detector.
 *
 * Topology of every case below: the relay heartbeats normally through the idle
 * window (the fixed writer), THEN the process dies, so the last row it ever
 * wrote freezes at `updated_at` and ages from there.
 */
describe("dead relay is still detected — the idle exemption is bounded", () => {
  /** Anchored mid-morning, not at the 15:45 ET incident instant: these cases
   * age a frozen row by up to 90 minutes and must stay inside RTH the whole
   * time, or the probe goes `applicable: false` and asserts nothing. */
  const MORNING_START_MS = Date.parse("2026-08-10T14:00:00Z");
  const MORNING_END_MS = MORNING_START_MS + 10 * 60_000;

  function lastRowBeforeDeath(activeSubscriptions: number): RelayHealthRow {
    const run =
      activeSubscriptions === 0
        ? idleRunFor(DISCONNECT_PHASES[0].warmupMs, MORNING_START_MS, MORNING_END_MS)
        : runRelayStaleCheckLoop({
            startMs: MORNING_START_MS,
            phases: [
              {
                durationMs: MORNING_END_MS - MORNING_START_MS,
                activeSubscriptions,
                ticksFlowing: true,
              },
            ],
          });
    if (!run.row) throw new Error("writer emitted no row — harness precondition failed");
    return run.row;
  }

  function frozenAtMs(row: RelayHealthRow): number {
    return Date.parse(row.updated_at as string);
  }

  /** An absolute "nobody could call this alive" silence, deliberately NOT
   * expressed as a multiple of the bound under test: a future widening of
   * RELAY_IDLE_HEARTBEAT_STALE_MS past 90 minutes of RTH silence must fail
   * here rather than move the goalposts with itself. */
  const DEAD_SILENCE_MS = 90 * 60_000;

  it("killed while idle: row frozen far past any plausible heartbeat still fails the probe", () => {
    const row = lastRowBeforeDeath(0);
    const probeAtMs = frozenAtMs(row) + DEAD_SILENCE_MS;

    expect(getMarketStateFromDate(new Date(probeAtMs)), "probe must land inside RTH").toBe("open");
    expect(
      JSON.parse(row.last_error as string).active_subscriptions,
      "this is the exempted zero-subscriber shape, not a subscribed one",
    ).toBe(0);
    expect(
      RELAY_IDLE_HEARTBEAT_STALE_MS,
      "the idle exemption must stay far tighter than an outright dead relay",
    ).toBeLessThan(DEAD_SILENCE_MS);

    const payload = buildFreshnessPayload(freshnessInputsFor(row, probeAtMs), new Date(probeAtMs));
    expect(
      payload.checks.relay_tick.fresh,
      `relay row is ${DEAD_SILENCE_MS}ms old with zero subscribers; the idle exemption ` +
        `(bound ${RELAY_IDLE_HEARTBEAT_STALE_MS}ms) must NOT read a dead relay process as fresh`,
    ).toBe(false);
    expect(payload.all_fresh, "a dead relay must still fail the probe's aggregate").toBe(false);
  });

  it("pins the idle detection bound: fresh just inside it, stale just past it", () => {
    const row = lastRowBeforeDeath(0);
    const frozen = frozenAtMs(row);

    expect(evaluateRelayTick(row, "open", frozen + RELAY_IDLE_HEARTBEAT_STALE_MS - 60_000).fresh).toBe(true);
    expect(evaluateRelayTick(row, "open", frozen + RELAY_IDLE_HEARTBEAT_STALE_MS + 60_000).fresh).toBe(false);
  });

  it("killed with subscriptions outstanding stays on the tighter tick-flow guard", () => {
    const row = lastRowBeforeDeath(WARM_SUBSCRIPTIONS);
    const frozen = frozenAtMs(row);

    expect(JSON.parse(row.last_error as string).active_subscriptions).toBe(WARM_SUBSCRIPTIONS);
    expect(evaluateRelayTick(row, "open", frozen + RELAY_HEARTBEAT_STALE_MS + 60_000).fresh).toBe(false);
  });

  it("never started: no row at all fails the probe", () => {
    expect(evaluateRelayTick(null, "open", DETECTION_MS)).toEqual({
      applicable: true,
      age_secs: null,
      fresh: false,
    });
  });

  it("latched error row fails regardless of subscriber count or heartbeat age", () => {
    const escalated: RelayHealthRow = {
      state: "error",
      last_error: JSON.stringify({
        error: "No ticks for 196s with 36 active subscriptions after 3 reconnect cycles",
        last_tick_at: iso(DETECTION_MS - 196_000),
        active_subscriptions: 0,
      }),
      updated_at: iso(DETECTION_MS - 10_000),
    };

    expect(
      evaluateRelayTick(escalated, "open", DETECTION_MS).fresh,
      "the escalation row is checked before the zero-subscriber exemption; a latched error is never fresh",
    ).toBe(false);
  });
});

/**
 * Open-bell + leftover farm-DOWN still freeze the same row the idle
 * heartbeat already learned to keep moving. The writer is RTH-only, so
 * yesterday's 16:00 ET close is a legitimate last write; a leftover
 * 2103/2105/2108 must not then silence the first idle cycles after drain.
 */
const YESTERDAY_CLOSE_MS = Date.parse("2026-08-12T20:00:00Z"); // Wed 16:00 ET
const OPEN_BELL_MS = Date.parse("2026-08-13T13:30:00Z"); // Thu 09:30:00 ET
const SUBSCRIBED_DRAIN_COUNT = 36;

function openBellCloseRow(): RelayHealthRow {
  const run = runRelayStaleCheckLoop({
    startMs: YESTERDAY_CLOSE_MS - 10 * 60_000,
    phases: [
      {
        durationMs: 10 * 60_000,
        activeSubscriptions: 0,
        ticksFlowing: false,
      },
    ],
  });
  if (!run.row) throw new Error("writer emitted no close-of-day row");
  return run.row;
}

describe("open-bell grace — overnight silence is not a dead relay", () => {
  it("pins yesterday close → next open bell as an RTH → RTH gap", () => {
    expect(isMarketOpen(new Date(YESTERDAY_CLOSE_MS))).toBe(true);
    expect(isMarketOpen(new Date(OPEN_BELL_MS))).toBe(true);
    expect(getMarketStateFromDate(new Date(OPEN_BELL_MS))).toBe("open");
    expect(OPEN_BELL_MS - YESTERDAY_CLOSE_MS).toBeGreaterThan(RELAY_HEARTBEAT_STALE_MS);
  });

  it("last write 16:00 ET yesterday is not relay_tick stale inside RELAY_HEARTBEAT_STALE_MS of the bell", () => {
    const row = openBellCloseRow();
    const frozen = Date.parse(row.updated_at as string);
    expect(frozen).toBeLessThanOrEqual(YESTERDAY_CLOSE_MS + STALE_CHECK_INTERVAL_MS);
    expect(frozen).toBeGreaterThan(YESTERDAY_CLOSE_MS - 2 * TICK_HEARTBEAT_INTERVAL_MS);

    const atBell = evaluateRelayTick(row, "open", OPEN_BELL_MS);
    const insideGrace = evaluateRelayTick(
      row,
      "open",
      OPEN_BELL_MS + RELAY_HEARTBEAT_STALE_MS - 60_000,
    );

    expect(atBell.applicable).toBe(true);
    expect(
      atBell.fresh,
      "09:30:00 ET must not compare overnight age against the 5m RTH heartbeat bound",
    ).toBe(true);
    expect(insideGrace.fresh).toBe(true);

    const payload = buildFreshnessPayload(freshnessInputsFor(row, OPEN_BELL_MS), new Date(OPEN_BELL_MS));
    expect(payload.checks.relay_tick.fresh).toBe(true);
    expect(payload.all_fresh).toBe(true);
  });

  it("control: the same frozen close row is stale once the session has been open past the bound", () => {
    const row = openBellCloseRow();
    const pastGrace = OPEN_BELL_MS + RELAY_HEARTBEAT_STALE_MS + 60_000;
    expect(getMarketStateFromDate(new Date(pastGrace))).toBe("open");
    expect(evaluateRelayTick(row, "open", pastGrace).fresh).toBe(false);
  });
});

describe("drain and leftover farm-DOWN do not freeze the idle heartbeat", () => {
  it("last subscribed write (subs=36) then drain to 0 keeps emitting", () => {
    const warmupMs = 5 * 60_000;
    const disconnectMs = SESSION_START_MS + warmupMs;
    const run = runRelayStaleCheckLoop({
      startMs: SESSION_START_MS,
      phases: [
        { durationMs: warmupMs, activeSubscriptions: SUBSCRIBED_DRAIN_COUNT, ticksFlowing: true },
        { durationMs: DETECTION_MS - disconnectMs, activeSubscriptions: 0, ticksFlowing: false },
      ],
    });

    const lastSubscribed = [...run.writes].reverse().find((w) => w.activeSubscriptions === SUBSCRIBED_DRAIN_COUNT);
    const idleWrites = run.writes.filter((w) => w.atMs > disconnectMs);

    expect(lastSubscribed).toBeDefined();
    expect(idleWrites.length).toBeGreaterThan(0);
    expect(idleWrites.every((w) => w.activeSubscriptions === 0)).toBe(true);
    expect(maxObservedWriteGapMs(run.writes, disconnectMs, DETECTION_MS)).toBeLessThanOrEqual(
      RELAY_HEARTBEAT_STALE_MS,
    );
    expect(evaluateRelayTick(run.row, "open", DETECTION_MS).fresh).toBe(true);
  });

  it("sticky farm 2105 then idle with ibConnected keeps the heartbeat", () => {
    const warmupMs = 5 * 60_000;
    const disconnectMs = SESSION_START_MS + warmupMs;
    const run = runRelayStaleCheckLoop({
      startMs: SESSION_START_MS,
      farmState: 2105,
      phases: [
        { durationMs: warmupMs, activeSubscriptions: SUBSCRIBED_DRAIN_COUNT, ticksFlowing: true },
        { durationMs: DETECTION_MS - disconnectMs, activeSubscriptions: 0, ticksFlowing: false },
      ],
    });

    const idleWrites = run.writes.filter((w) => w.atMs > disconnectMs);
    expect(
      idleWrites.length,
      "leftover 2105 must not skip the 60s idle heartbeat once demand is gone",
    ).toBeGreaterThan(0);
    expect(maxObservedWriteGapMs(run.writes, disconnectMs, DETECTION_MS)).toBeLessThanOrEqual(
      RELAY_HEARTBEAT_STALE_MS,
    );
    expect(evaluateRelayTick(run.row, "open", DETECTION_MS).fresh).toBe(true);
  });

  it("dead process and latched error still read fresh=false", () => {
    const live = idleRunFor(DISCONNECT_PHASES[0].warmupMs, Date.parse("2026-08-10T14:00:00Z"), Date.parse("2026-08-10T14:10:00Z"));
    if (!live.row) throw new Error("writer emitted no idle row");
    const deadProbe = Date.parse(live.row.updated_at as string) + 90 * 60_000;
    expect(evaluateRelayTick(live.row, "open", deadProbe).fresh).toBe(false);

    const latched: RelayHealthRow = {
      state: "error",
      last_error: JSON.stringify({
        error: "No ticks for 196s with 36 active subscriptions after 3 reconnect cycles",
        last_tick_at: iso(OPEN_BELL_MS - 196_000),
        active_subscriptions: 0,
      }),
      updated_at: iso(OPEN_BELL_MS - 10_000),
    };
    expect(evaluateRelayTick(latched, "open", OPEN_BELL_MS).fresh).toBe(false);
  });
});
