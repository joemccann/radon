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
