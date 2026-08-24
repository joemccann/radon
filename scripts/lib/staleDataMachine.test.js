import { describe, it, expect } from "vitest";
import {
  decideStaleAction,
  isFarmStateCode,
  STALE_DATA_THRESHOLD_MS,
  STALE_CHECK_INTERVAL_MS,
  MAX_RECONNECT_CYCLES,
  ESCALATION_COOLDOWN_MS,
  FARM_OK_CODES,
  FARM_DOWN_CODES,
  shouldWriteTickHeartbeat,
  TICK_HEARTBEAT_INTERVAL_MS,
  decideHealthWrite,
  shouldRequestGatewayRestart,
  summarizeSubscriptionFreshness,
  nextFarmStateCode,
  farmStateAfterIdleDrain,
} from "./staleDataMachine.js";

const NOW = 1_700_000_000_000;

// A healthy-looking baseline; individual tests override only what they probe.
function input(overrides = {}) {
  return {
    now: NOW,
    lastTickAt: NOW, // fresh
    ibConnected: true,
    isMarketHours: true,
    activeSubscriptions: 35,
    reconnectCycles: 0,
    farmState: null,
    lastEscalationAt: null,
    ...overrides,
  };
}

// Stale by construction: last tick older than the threshold.
const STALE_TICK_AT = NOW - (STALE_DATA_THRESHOLD_MS + 5_000);

describe("staleDataMachine constants", () => {
  it("pins the documented thresholds + ladder constants", () => {
    expect(STALE_DATA_THRESHOLD_MS).toBe(45_000);
    expect(STALE_CHECK_INTERVAL_MS).toBe(30_000);
    expect(MAX_RECONNECT_CYCLES).toBe(3);
    expect(ESCALATION_COOLDOWN_MS).toBe(900_000);
  });

  it("classifies farm OK + farm down info codes", () => {
    for (const code of [2104, 2106, 2158]) {
      expect(FARM_OK_CODES.has(code)).toBe(true);
      expect(isFarmStateCode(code)).toBe(true);
    }
    for (const code of [2103, 2105, 2108]) {
      expect(FARM_DOWN_CODES.has(code)).toBe(true);
      expect(isFarmStateCode(code)).toBe(true);
    }
    expect(isFarmStateCode(354)).toBe(false);
  });
});

describe("decideStaleAction", () => {
  it("per-subscription staleness cannot be masked or escalate unentitled subjects", () => {
    const summary = summarizeSubscriptionFreshness([
      { active: true, lastTickAt: NOW },
      { active: true, lastTickAt: STALE_TICK_AT },
      { active: false, lastTickAt: 0 },
    ], NOW);

    expect(summary).toEqual({ activeSubscriptions: 2, subscribedSymbols: 3, lastTickAt: STALE_TICK_AT });
    expect(decideStaleAction(input(summary))).toBe("reconnect");

    expect(summarizeSubscriptionFreshness([
      { active: false, lastTickAt: STALE_TICK_AT },
    ], NOW)).toEqual({ activeSubscriptions: 0, subscribedSymbols: 1, lastTickAt: STALE_TICK_AT });
  });

  it("healthy ticks → none", () => {
    expect(decideStaleAction(input())).toBe("none");
  });

  it("ticks exactly at the threshold → none (not yet stale)", () => {
    expect(
      decideStaleAction(input({ lastTickAt: NOW - STALE_DATA_THRESHOLD_MS })),
    ).toBe("none");
  });

  it("no active subscriptions during warm-up → none (no false alert)", () => {
    expect(
      decideStaleAction(input({ lastTickAt: STALE_TICK_AT, activeSubscriptions: 0 })),
    ).toBe("none");
  });

  it("disconnected socket → none (reconnect path owns recovery)", () => {
    expect(
      decideStaleAction(input({ lastTickAt: STALE_TICK_AT, ibConnected: false })),
    ).toBe("none");
  });

  it("market closed → none even with stale ticks (no off-hours false alert)", () => {
    expect(
      decideStaleAction(
        input({ lastTickAt: STALE_TICK_AT, isMarketHours: false, reconnectCycles: 99 }),
      ),
    ).toBe("none");
  });

  it("brief no-ticks, no farm hint → reconnect", () => {
    expect(decideStaleAction(input({ lastTickAt: STALE_TICK_AT }))).toBe("reconnect");
  });

  it("stale ticks but farm-OK signal → prefer resubscribe over socket bounce", () => {
    expect(
      decideStaleAction(input({ lastTickAt: STALE_TICK_AT, farmState: 2104 })),
    ).toBe("resubscribe");
  });

  it("farm-OK resubscribe wins even after exhausting reconnect cycles", () => {
    expect(
      decideStaleAction(
        input({
          lastTickAt: STALE_TICK_AT,
          farmState: 2106,
          reconnectCycles: MAX_RECONNECT_CYCLES,
        }),
      ),
    ).toBe("resubscribe");
  });

  it("farm-DOWN code does not divert to resubscribe → reconnect", () => {
    expect(
      decideStaleAction(input({ lastTickAt: STALE_TICK_AT, farmState: 2103 })),
    ).toBe("reconnect");
  });

  it("reconnects up to K times, then escalates on the (K+1)th stale cycle", () => {
    for (let cycles = 0; cycles < MAX_RECONNECT_CYCLES; cycles += 1) {
      expect(
        decideStaleAction(input({ lastTickAt: STALE_TICK_AT, reconnectCycles: cycles })),
      ).toBe("reconnect");
    }
    expect(
      decideStaleAction(
        input({ lastTickAt: STALE_TICK_AT, reconnectCycles: MAX_RECONNECT_CYCLES }),
      ),
    ).toBe("escalate");
  });

  it("escalation respects cooldown — no stacking within the window", () => {
    expect(
      decideStaleAction(
        input({
          lastTickAt: STALE_TICK_AT,
          reconnectCycles: MAX_RECONNECT_CYCLES,
          lastEscalationAt: NOW - (ESCALATION_COOLDOWN_MS - 1),
        }),
      ),
    ).toBe("none");
  });

  it("escalates again once the cooldown has elapsed", () => {
    expect(
      decideStaleAction(
        input({
          lastTickAt: STALE_TICK_AT,
          reconnectCycles: MAX_RECONNECT_CYCLES,
          lastEscalationAt: NOW - ESCALATION_COOLDOWN_MS,
        }),
      ),
    ).toBe("escalate");
  });

  it("recovery: fresh ticks after escalation → none (caller clears error + resets counters)", () => {
    // Counters are still high but ticks have resumed; the machine reports
    // 'none' and the relay's onTicksRecovered() clears the error row.
    expect(
      decideStaleAction(
        input({
          lastTickAt: NOW, // fresh again
          reconnectCycles: MAX_RECONNECT_CYCLES,
          lastEscalationAt: NOW - 1_000,
        }),
      ),
    ).toBe("none");
  });
});

describe("shouldWriteTickHeartbeat (DUR-16 freshness-probe heartbeat)", () => {
  function hbInput(overrides = {}) {
    return {
      now: NOW,
      isMarketHours: true,
      inError: false,
      lastHeartbeatAt: NOW - TICK_HEARTBEAT_INTERVAL_MS,
      ...overrides,
    };
  }

  it("writes during RTH once the interval has elapsed", () => {
    expect(shouldWriteTickHeartbeat(hbInput())).toBe(true);
    expect(
      shouldWriteTickHeartbeat(hbInput({ lastHeartbeatAt: NOW - TICK_HEARTBEAT_INTERVAL_MS - 1 })),
    ).toBe(true);
  });

  it("throttles inside the interval", () => {
    expect(
      shouldWriteTickHeartbeat(hbInput({ lastHeartbeatAt: NOW - TICK_HEARTBEAT_INTERVAL_MS + 1 })),
    ).toBe(false);
    expect(shouldWriteTickHeartbeat(hbInput({ lastHeartbeatAt: NOW }))).toBe(false);
  });

  it("never writes off-hours — the relay stays event-driven outside RTH", () => {
    expect(shouldWriteTickHeartbeat(hbInput({ isMarketHours: false }))).toBe(false);
  });

  it("never writes while the error row is latched — the ladder owns that edge", () => {
    expect(shouldWriteTickHeartbeat(hbInput({ inError: true }))).toBe(false);
  });

  it("fires immediately on the first RTH cycle of a fresh process (lastHeartbeatAt=0)", () => {
    expect(shouldWriteTickHeartbeat(hbInput({ lastHeartbeatAt: 0 }))).toBe(true);
  });
});

describe("decideHealthWrite (heartbeat must never clobber the escalation row)", () => {
  // A healthy baseline that WOULD write a heartbeat (interval elapsed, RTH, no error).
  function hw(overrides = {}) {
    return {
      now: NOW,
      lastTickAt: NOW, // fresh
      ibConnected: true,
      isMarketHours: true,
      activeSubscriptions: 35,
      reconnectCycles: 0,
      farmState: null,
      lastEscalationAt: null,
      inError: false,
      lastHeartbeatAt: NOW - TICK_HEARTBEAT_INTERVAL_MS,
      ...overrides,
    };
  }

  it("healthy cycle → heartbeat ok, no recovery action", () => {
    expect(decideHealthWrite(hw())).toEqual({ action: "none", heartbeat: true, clearError: false, degraded: false, disconnected: false });
  });

  it("disconnected relay with an old tick → no misleading ok heartbeat", () => {
    // Reconnect owns socket recovery, so the action remains none. It must not
    // publish a fresh-looking tick heartbeat while the data plane is offline.
    expect(
      decideHealthWrite(
        hw({
          ibConnected: false,
          lastTickAt: STALE_TICK_AT,
          lastHeartbeatAt: NOW - TICK_HEARTBEAT_INTERVAL_MS - 10_000,
        }),
      ),
    ).toEqual({ action: "none", heartbeat: false, clearError: false, degraded: false, disconnected: false });
  });

  it("ESCALATION cycle → NO heartbeat (the 2026-06-18 clobber bug)", () => {
    // Stale + K cycles burned + no cooldown + heartbeat interval elapsed +
    // error not yet latched (escalateStaleData latches it AFTER this decision).
    // Pre-fix the heartbeat ok would race the escalation error; it must not.
    expect(
      decideHealthWrite(
        hw({
          lastTickAt: STALE_TICK_AT,
          reconnectCycles: MAX_RECONNECT_CYCLES,
          inError: false,
          lastHeartbeatAt: NOW - TICK_HEARTBEAT_INTERVAL_MS - 10_000,
        }),
      ),
    ).toEqual({ action: "escalate", heartbeat: false, clearError: false, degraded: false, disconnected: false });
  });

  it("reconnect cycle → no heartbeat (ladder owns the row)", () => {
    expect(
      decideHealthWrite(hw({ lastTickAt: STALE_TICK_AT, reconnectCycles: 0 })),
    ).toEqual({ action: "reconnect", heartbeat: false, clearError: false, degraded: false, disconnected: false });
  });

  it("resubscribe cycle → no heartbeat", () => {
    expect(
      decideHealthWrite(hw({ lastTickAt: STALE_TICK_AT, farmState: 2104 })),
    ).toEqual({ action: "resubscribe", heartbeat: false, clearError: false, degraded: false, disconnected: false });
  });

  it("post-escalation cooldown (still stale, error latched) → action none but NO heartbeat", () => {
    // decideStaleAction returns 'none' inside the cooldown even though ticks
    // are still stale; the inError guard must keep the heartbeat from
    // resurrecting the row to ok.
    expect(
      decideHealthWrite(
        hw({
          lastTickAt: STALE_TICK_AT,
          reconnectCycles: MAX_RECONNECT_CYCLES,
          lastEscalationAt: NOW - (ESCALATION_COOLDOWN_MS - 1),
          inError: true,
        }),
      ),
    ).toEqual({ action: "none", heartbeat: false, clearError: false, degraded: false, disconnected: false });
  });

  it("idle relay (zero subscribers, no tick possible) → still heartbeats", () => {
    // The row reports THIS writer's state, not the tick stream's. With no
    // subscribers no reqMktData is outstanding, so lastTickAt can never advance;
    // gating the heartbeat on it froze the row for the whole idle window and
    // read downstream as a dead relay (2026-08-10 stale-freshness incident).
    expect(
      decideHealthWrite(
        hw({ activeSubscriptions: 0, lastTickAt: STALE_TICK_AT }),
      ),
    ).toEqual({ action: "none", heartbeat: true, clearError: false, degraded: false, disconnected: false });
  });

  it("idle relay with the IB socket DOWN → never looks idle-healthy", () => {
    // Guard ORDER is load-bearing: checking "no subscribers" before "socket up"
    // would publish ok heartbeats every 60s forever from a relay whose IB
    // connection is gone — the frozen row is the only remaining detector.
    expect(
      decideHealthWrite(
        hw({ ibConnected: false, activeSubscriptions: 0, lastTickAt: STALE_TICK_AT }),
      ),
    ).toEqual({ action: "none", heartbeat: false, clearError: false, degraded: false, disconnected: false });
  });

  it("sticky leftover farm-DOWN while idle still heartbeats", () => {
    // lastFarmStateCode sticks across drain / overnight. A leftover 2103/2105/2108
    // is not a current farm-down, and gating the idle heartbeat on it freezes
    // the row the same way the 2026-08-10 tick-age gate did.
    for (const farmState of [2103, 2105, 2108]) {
      expect(
        decideHealthWrite(
          hw({ activeSubscriptions: 0, lastTickAt: STALE_TICK_AT, farmState }),
        ),
      ).toEqual({ action: "none", heartbeat: true, clearError: false, degraded: false, disconnected: false });
    }
  });

  it("escalated then went idle → clears the latch the tick edge can never reach", () => {
    // onTicksRecovered is the only other way out of inError, and it fires only
    // from markTick, which needs an outstanding reqMktData. Once the last client
    // is reaped there is none, so without this edge the row stays "error" for
    // hours against a relay that is merely idle.
    expect(
      decideHealthWrite(
        hw({
          activeSubscriptions: 0,
          lastTickAt: STALE_TICK_AT,
          inError: true,
          reconnectCycles: MAX_RECONNECT_CYCLES,
          lastEscalationAt: NOW - (ESCALATION_COOLDOWN_MS - 1),
        }),
      ),
    ).toEqual({ action: "none", heartbeat: false, clearError: true, degraded: false, disconnected: false });
  });

  it("escalated then idle clears the latch even with a leftover farm-DOWN code", () => {
    expect(
      decideHealthWrite(
        hw({
          activeSubscriptions: 0,
          lastTickAt: STALE_TICK_AT,
          farmState: 2103,
          inError: true,
          reconnectCycles: MAX_RECONNECT_CYCLES,
          lastEscalationAt: NOW - (ESCALATION_COOLDOWN_MS - 1),
        }),
      ),
    ).toEqual({ action: "none", heartbeat: false, clearError: true, degraded: false, disconnected: false });
  });

  it("healthy but inside the heartbeat interval → no write, no action", () => {
    expect(
      decideHealthWrite(hw({ lastHeartbeatAt: NOW - 1_000 })),
    ).toEqual({ action: "none", heartbeat: false, clearError: false, degraded: false, disconnected: false });
  });
});

describe("R-061 blackout honesty (every subscription nulled by IB is degraded, not idle)", () => {
  // The relay's staleCheckTimer builds one subject per symbolSubscribers key.
  // IB error 200/354 nulls state.tickerId (active: false) but the symbol stays
  // subscribed — this is the acceptance scenario: 3 subscribed symbols, IB
  // 354s all of them.
  const NULLED_SUBJECTS = [
    { active: false, lastTickAt: STALE_TICK_AT },
    { active: false, lastTickAt: STALE_TICK_AT + 1_000 },
    { active: false, lastTickAt: STALE_TICK_AT + 2_000 },
  ];

  function nulled(overrides = {}) {
    return {
      now: NOW,
      lastTickAt: STALE_TICK_AT,
      ibConnected: true,
      isMarketHours: true,
      activeSubscriptions: 0,
      subscribedSymbols: 3,
      reconnectCycles: 0,
      farmState: null,
      lastEscalationAt: null,
      inError: false,
      lastHeartbeatAt: NOW - TICK_HEARTBEAT_INTERVAL_MS,
      ...overrides,
    };
  }

  it("summarize keeps the REAL tick age when demand remains — no idle substitution", () => {
    expect(summarizeSubscriptionFreshness(NULLED_SUBJECTS, NOW)).toEqual({
      activeSubscriptions: 0,
      subscribedSymbols: 3,
      lastTickAt: STALE_TICK_AT,
    });
  });

  it("summarize still substitutes now only when NOTHING is subscribed (true idle)", () => {
    expect(summarizeSubscriptionFreshness([], NOW)).toEqual({
      activeSubscriptions: 0,
      subscribedSymbols: 0,
      lastTickAt: NOW,
    });
  });

  it("all subscriptions nulled during RTH → degraded, never an ok heartbeat", () => {
    expect(decideHealthWrite(nulled())).toEqual({
      action: "none",
      heartbeat: false,
      clearError: false,
      degraded: true,
      disconnected: false,
    });
  });

  it("a nulled data plane must NOT clear a latched escalation", () => {
    expect(decideHealthWrite(nulled({ inError: true }))).toEqual({
      action: "none",
      heartbeat: false,
      clearError: false,
      degraded: true,
      disconnected: false,
    });
  });

  it("true idle (zero subscribed symbols) still heartbeats — 2026-08-10 fix preserved", () => {
    expect(decideHealthWrite(nulled({ subscribedSymbols: 0, lastTickAt: STALE_TICK_AT }))).toEqual({
      action: "none",
      heartbeat: true,
      clearError: false,
      degraded: false,
      disconnected: false,
    });
  });

  it("nulled subscriptions with a still-fresh tick are inside the grace window", () => {
    expect(decideHealthWrite(nulled({ lastTickAt: NOW - 1_000 }))).toEqual({
      action: "none",
      heartbeat: true,
      clearError: false,
      degraded: false,
      disconnected: false,
    });
  });

  it("off-hours nulled subscriptions never write a degraded row", () => {
    expect(decideHealthWrite(nulled({ isMarketHours: false })).degraded).toBe(false);
  });

  it("disconnected socket is the reconnect path's problem, not degraded", () => {
    expect(decideHealthWrite(nulled({ ibConnected: false })).degraded).toBe(false);
  });

  it("nulled subjects never escalate the ladder (unentitled subjects stay non-actionable)", () => {
    expect(
      decideStaleAction(input({ activeSubscriptions: 0, subscribedSymbols: 3, lastTickAt: STALE_TICK_AT })),
    ).toBe("none");
  });
});

describe("farm-state hygiene (leftover DOWN must not stick into idle)", () => {
  it("clears leftover DOWN on farm-OK when already idle", () => {
    expect(nextFarmStateCode(2105, 2104, 0)).toBeNull();
    expect(nextFarmStateCode(2103, 2106, 0)).toBeNull();
    expect(nextFarmStateCode(2108, 2158, 0)).toBeNull();
  });

  it("keeps farm-OK while subscribed so the ladder can resubscribe", () => {
    expect(nextFarmStateCode(2105, 2104, 36)).toBe(2104);
    expect(nextFarmStateCode(null, 2106, 1)).toBe(2106);
  });

  it("records a new farm-DOWN whether idle or subscribed", () => {
    expect(nextFarmStateCode(2104, 2105, 0)).toBe(2105);
    expect(nextFarmStateCode(null, 2103, 12)).toBe(2103);
  });

  it("ignores non-farm info codes", () => {
    expect(nextFarmStateCode(2105, 354, 0)).toBe(2105);
  });

  it("clears farm state on idle drain", () => {
    expect(farmStateAfterIdleDrain()).toBeNull();
  });
});

describe("shouldRequestGatewayRestart (relay may only restart container-owned gateways)", () => {
  it("docker + cloud → relay drives the lock-held restart", () => {
    expect(shouldRequestGatewayRestart("docker")).toBe(true);
    expect(shouldRequestGatewayRestart("cloud")).toBe(true);
  });

  it("launchd / local / unset → alert-only, never restart from the relay", () => {
    expect(shouldRequestGatewayRestart("launchd")).toBe(false);
    expect(shouldRequestGatewayRestart("local")).toBe(false);
    expect(shouldRequestGatewayRestart(undefined)).toBe(false);
  });
});
