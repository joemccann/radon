/**
 * REL-035 / finding R-061 — relay blackout honesty.
 *
 * Commit 3cd40d18 redefined an active subscription as `state.tickerId != null`,
 * but the IB error handler nulls tickerId on code 200 and 354 while leaving the
 * symbol in `symbolSubscribers`. An entitlement/farm failure that 354s every
 * symbol therefore collapses activeSubscriptions to 0 while demand is fully
 * outstanding. Pre-fix, the machine read that as IDLE: the summarize helper
 * substituted lastTickAt=now, hasHealthyDataPlane returned true, the relay kept
 * writing `ib-realtime-relay=ok` with `tick_age_secs: 0` (while `last_tick_at`
 * carried the real, frozen timestamp — a self-contradictory row), clearError
 * cleared latched escalations, and evaluateRelayTick's zero-subscription
 * exemption reported fresh. Every browser showed frozen prices behind green
 * health.
 *
 * This suite composes the REAL writer decision (summarizeSubscriptionFreshness
 * + decideHealthWrite) and the REAL row builder (buildRelayHealthDetail, the
 * one the relay spreads into both write branches) with the REAL evaluator
 * (evaluateRelayTick), replaying the relay's stale-check loop
 * (ib_realtime_server.js) including its degraded write branch — the same
 * seam-level topology as relay-idle-heartbeat-freshness.test.ts, because each
 * side's unit tests were individually green while the composition lied.
 *
 * T-087: the payload itself is never hand-mirrored here. The relay cannot be
 * imported without opening its sockets, so the builder is the seam, and a
 * source guard pins the relay to it.
 */
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import {
  buildRelayHealthDetail,
  decideHealthWrite,
  STALE_CHECK_INTERVAL_MS,
  STALE_DATA_THRESHOLD_MS,
  summarizeSubscriptionFreshness,
  TICK_HEARTBEAT_INTERVAL_MS,
} from "../../scripts/lib/staleDataMachine.js";
import { isMarketOpen } from "../../scripts/lib/marketCalendar.js";
import { evaluateRelayTick, type RelayHealthRow } from "../lib/probeFreshness";
import { getMarketStateFromDate } from "../lib/serviceHealthWindows";

/** Mid-morning RTH on a known trading day (same anchor as the idle-heartbeat
 * suite) so isMarketOpen and the probe's market state both hold throughout. */
const SESSION_START_MS = Date.parse("2026-08-10T14:00:00Z");

/** The acceptance scenario: three subscribed symbols. */
const SYMBOLS = ["SPY", "QQQ", "VIX"] as const;

type SymbolState = { tickerId: number | null; lastTickAt: number };

type HealthWrite = {
  atMs: number;
  state: "ok" | "error";
  detail: Record<string, unknown>;
};

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

/**
 * Replay of the relay's stale-check cycle over a pinned clock, mirroring the
 * ib_realtime_server.js call site: subjects are built from symbolSubscribers ×
 * symbolStates (`active: tickerId != null`), decideHealthWrite chooses, and the
 * write branches spread the REAL buildRelayHealthDetail output — ok heartbeat
 * and degraded error alike — exactly as the relay does.
 */
function runRelayLoop({
  startMs,
  phases,
}: {
  startMs: number;
  phases: Array<{ durationMs: number; ticksFlowing: boolean; nulled: boolean; inErrorAtStart?: boolean }>;
}) {
  const states = new Map<string, SymbolState>(
    SYMBOLS.map((symbol, i) => [symbol, { tickerId: i + 1, lastTickAt: startMs }]),
  );
  let lastTickTimestamp = startMs;
  let lastHeartbeatAt = startMs;
  let inError = false;
  let clearErrorFired = false;
  let now = startMs;
  const writes: HealthWrite[] = [];

  for (const phase of phases) {
    if (phase.inErrorAtStart) inError = true;
    if (phase.nulled) {
      // IB 354s every symbol: the error handler nulls tickerId but the symbol
      // stays in symbolSubscribers.
      for (const state of states.values()) state.tickerId = null;
    } else {
      // Entitlement restored: resubscribeAll re-issues reqMktData tickets.
      let nextId = 1;
      for (const state of states.values()) state.tickerId = nextId++;
    }
    const phaseEnd = now + phase.durationMs;
    while (now < phaseEnd) {
      now += STALE_CHECK_INTERVAL_MS;
      if (phase.ticksFlowing) {
        lastTickTimestamp = now;
        for (const state of states.values()) state.lastTickAt = now;
      }

      const freshness = summarizeSubscriptionFreshness(
        [...states.values()].map((state) => ({
          active: state.tickerId != null,
          lastTickAt: state.lastTickAt,
        })),
        now,
      );
      const { activeSubscriptions, subscribedSymbols } = freshness;

      const { heartbeat, clearError, degraded } = decideHealthWrite({
        now,
        lastTickAt: freshness.lastTickAt,
        ibConnected: true,
        isMarketHours: isMarketOpen(new Date(now)),
        activeSubscriptions,
        subscribedSymbols,
        reconnectCycles: 0,
        farmState: null,
        lastEscalationAt: null,
        inError,
        lastHeartbeatAt,
      } as never);

      if (clearError) {
        inError = false;
        clearErrorFired = true;
      }

      if (heartbeat || clearError) {
        lastHeartbeatAt = now;
        writes.push({
          atMs: now,
          state: "ok",
          detail: {
            heartbeat: "tick",
            ...buildRelayHealthDetail(now, lastTickTimestamp, freshness),
          },
        });
      }

      if (degraded) {
        inError = true;
        writes.push({
          atMs: now,
          state: "error",
          detail: {
            message: `IB nulled all market-data subscriptions (${subscribedSymbols} symbols subscribed, 0 active)`,
            reason: "subscriptions_nulled",
            ...buildRelayHealthDetail(now, lastTickTimestamp, freshness),
          },
        });
      }
    }
  }

  const last = writes.at(-1) ?? null;
  const row: RelayHealthRow | null = last
    ? { state: last.state, last_error: JSON.stringify(last.detail), updated_at: iso(last.atMs) }
    : null;
  return { writes, row, inError, clearErrorFired, endMs: now };
}

const WARMUP_MS = 5 * 60_000;
const BLACKOUT_MS = 10 * 60_000;

function blackoutRun(opts: { inErrorAtStart?: boolean } = {}) {
  return runRelayLoop({
    startMs: SESSION_START_MS,
    phases: [
      { durationMs: WARMUP_MS, ticksFlowing: true, nulled: false },
      { durationMs: BLACKOUT_MS, ticksFlowing: false, nulled: true, ...opts },
    ],
  });
}

describe("T-087: buildRelayHealthDetail is the relay's row payload", () => {
  const lastTickTimestamp = SESSION_START_MS;
  const now = SESSION_START_MS + BLACKOUT_MS;

  it("derives last_tick_at AND tick_age_secs from lastTickTimestamp, carrying the freshness counts", () => {
    const freshness = { activeSubscriptions: 0, subscribedSymbols: SYMBOLS.length, lastTickAt: lastTickTimestamp };
    expect(buildRelayHealthDetail(now, lastTickTimestamp, freshness)).toEqual({
      last_tick_at: iso(lastTickTimestamp),
      tick_age_secs: BLACKOUT_MS / 1000,
      active_subscriptions: 0,
      subscribed_symbols: SYMBOLS.length,
    });
  });

  it("ignores freshness.lastTickAt — the idle substitution (R-061) must not zero the age", () => {
    const idle = summarizeSubscriptionFreshness([], now);
    expect(idle.lastTickAt, "precondition: idle summary substitutes now").toBe(now);
    const detail = buildRelayHealthDetail(now, lastTickTimestamp, idle);
    expect(detail.tick_age_secs).toBe(BLACKOUT_MS / 1000);
    expect(detail.last_tick_at).toBe(iso(lastTickTimestamp));
    expect(Math.round((now - Date.parse(detail.last_tick_at)) / 1000)).toBe(detail.tick_age_secs);
  });

  it("ignores freshness.lastTickAt when it disagrees with the relay's lastTickTimestamp", () => {
    const olderSubject = lastTickTimestamp - 4 * STALE_DATA_THRESHOLD_MS;
    const freshness = { activeSubscriptions: 2, subscribedSymbols: 3, lastTickAt: olderSubject };
    const detail = buildRelayHealthDetail(now, lastTickTimestamp, freshness);
    expect(detail.last_tick_at).toBe(iso(lastTickTimestamp));
    expect(detail.tick_age_secs).toBe(BLACKOUT_MS / 1000);
    expect(detail).toMatchObject({ active_subscriptions: 2, subscribed_symbols: 3 });
  });

  it("source guard: the relay spreads the builder into both write branches and hand-rolls neither field", () => {
    // Supplement to the behavioural cases above: ib_realtime_server.js opens
    // sockets on import, so pin the call site textually instead.
    const relaySource = readFileSync(new URL("../../scripts/ib_realtime_server.js", import.meta.url), "utf8");
    const builderCalls = relaySource.match(/\.\.\.buildRelayHealthDetail\(now, lastTickTimestamp, freshness\)/g) ?? [];
    expect(builderCalls, "ok heartbeat + degraded error rows").toHaveLength(2);
    expect(relaySource).not.toMatch(/tick_age_secs:/);
    expect(relaySource).not.toMatch(/last_tick_at:/);
  });
});

describe("REL-035: IB 354s every subscription — the relay must not report idle-healthy", () => {
  it("pins the scenario inside RTH on both clocks", () => {
    expect(isMarketOpen(new Date(SESSION_START_MS))).toBe(true);
    expect(getMarketStateFromDate(new Date(SESSION_START_MS + WARMUP_MS + BLACKOUT_MS))).toBe("open");
  });

  it("writes a NON-ok service_health row once the blackout exceeds the stale threshold", () => {
    const { writes, row } = blackoutRun();
    const blackoutStart = SESSION_START_MS + WARMUP_MS;
    const okDuringBlackout = writes.filter(
      (w) => w.state === "ok" && w.atMs > blackoutStart + STALE_DATA_THRESHOLD_MS,
    );

    expect(row?.state, "the last row of the blackout window must be non-ok").toBe("error");
    expect(
      okDuringBlackout,
      "no ok heartbeat may be written while every subscription is nulled and ticks are stale",
    ).toEqual([]);
    expect(writes.some((w) => w.state === "error")).toBe(true);
  });

  it("never clears a latched escalation while demand remains nulled", () => {
    // The latch exists once ticks are already stale (a fresh tick inside the
    // 45s grace legitimately settles the question, matching onTicksRecovered),
    // so the escalated phase starts after the threshold has passed.
    const { clearErrorFired, inError } = runRelayLoop({
      startMs: SESSION_START_MS,
      phases: [
        { durationMs: WARMUP_MS, ticksFlowing: true, nulled: false },
        { durationMs: 2 * STALE_CHECK_INTERVAL_MS, ticksFlowing: false, nulled: true },
        { durationMs: BLACKOUT_MS, ticksFlowing: false, nulled: true, inErrorAtStart: true },
      ],
    });
    expect(clearErrorFired, "clearError must not fire — the data plane is degraded, not idle").toBe(false);
    expect(inError).toBe(true);
  });

  it("last_tick_at and tick_age_secs come from the same source in every row", () => {
    const { writes } = blackoutRun();
    expect(writes.length).toBeGreaterThan(0);
    expect(writes.some((w) => w.state === "error")).toBe(true);
    for (const write of writes) {
      const fromTimestamp = Math.round((write.atMs - Date.parse(String(write.detail.last_tick_at))) / 1000);
      expect(write.detail.tick_age_secs, `row at ${iso(write.atMs)} is self-contradictory`).toBe(fromTimestamp);
    }
  });

  it("/api/probe/freshness reports the blackout row stale", () => {
    const { row, endMs } = blackoutRun();
    const check = evaluateRelayTick(row, "open", endMs);
    expect(check.applicable).toBe(true);
    expect(check.fresh, "frozen prices must never probe fresh").toBe(false);
  });

  it("defense-in-depth: even an ok row with all subscriptions nulled reads stale", () => {
    // If a pre-fix relay (or a race) writes ok with subscribed demand but zero
    // active subscriptions and a stale tick, the evaluator must not take the
    // idle exemption.
    const nowMs = SESSION_START_MS + WARMUP_MS + BLACKOUT_MS;
    const row: RelayHealthRow = {
      state: "ok",
      last_error: JSON.stringify({
        heartbeat: "tick",
        last_tick_at: iso(nowMs - BLACKOUT_MS),
        tick_age_secs: Math.round(BLACKOUT_MS / 1000),
        active_subscriptions: 0,
        subscribed_symbols: SYMBOLS.length,
      }),
      updated_at: iso(nowMs - 10_000), // writer demonstrably alive
    };
    expect(evaluateRelayTick(row, "open", nowMs).fresh).toBe(false);
  });

  it("control: ticks resume after the blackout → ok heartbeat returns and probes fresh", () => {
    const { row, endMs, writes } = runRelayLoop({
      startMs: SESSION_START_MS,
      phases: [
        { durationMs: WARMUP_MS, ticksFlowing: true, nulled: false },
        { durationMs: BLACKOUT_MS, ticksFlowing: false, nulled: true },
        // IB restores the entitlement: the relay resubscribes and ticks flow.
        { durationMs: 3 * TICK_HEARTBEAT_INTERVAL_MS, ticksFlowing: true, nulled: false },
      ],
    });
    // In the real relay the recovery ok is written by markTick→onTicksRecovered;
    // here the heartbeat path stands in once the plane is healthy again.
    expect(writes.at(-1)?.state).toBe("ok");
    expect(evaluateRelayTick(row, "open", endMs).fresh).toBe(true);
  });
});
