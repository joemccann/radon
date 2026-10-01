/**
 * IBKR operator hold (2026-10-01).
 *
 * In an emergency the operator flattens from IBKR Mobile, which takes the
 * IBKR username the Gateway shares, so the Gateway goes down on purpose. The
 * relay must not read that as an outage: no "IB socket disconnected" error row
 * (the watchdog pages it as P1) and no escalation to POST /ib/restart (a login
 * would kick the operator mid-flatten).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  applyOperatorHold,
  decideHealthWrite,
  MAX_RECONNECT_CYCLES,
  STALE_DATA_THRESHOLD_MS,
} from "../../scripts/lib/staleDataMachine.js";

const NOW = 1_800_000_000_000;
const RELAY = readFileSync(resolve(__dirname, "../../scripts/ib_realtime_server.js"), "utf8");

function input(over: Record<string, unknown> = {}) {
  return {
    now: NOW,
    lastTickAt: NOW - STALE_DATA_THRESHOLD_MS - 60_000,
    ibConnected: false,
    isMarketHours: true,
    activeSubscriptions: 0,
    subscribedSymbols: 12,
    reconnectCycles: 0,
    farmState: undefined,
    lastEscalationAt: 0,
    inError: false,
    lastHeartbeatAt: 0,
    disconnectedSinceAt: NOW - STALE_DATA_THRESHOLD_MS - 60_000,
    ...over,
  };
}

describe("relay during an IBKR operator hold", () => {
  it("a held Gateway is not reported disconnected", () => {
    expect(decideHealthWrite(input()).disconnected).toBe(true);
    const held = applyOperatorHold(decideHealthWrite(input()), true);
    expect(held.held).toBe(true);
    expect(held.disconnected).toBe(false);
  });

  it("a held Gateway is never escalated to a restart", () => {
    const escalating = decideHealthWrite(
      input({ ibConnected: true, activeSubscriptions: 12, reconnectCycles: MAX_RECONNECT_CYCLES }),
    );
    expect(escalating.action).toBe("escalate");
    expect(applyOperatorHold(escalating, true).action).toBe("none");
  });

  it("the relay feeds every decision through the hold before acting on it", () => {
    expect(RELAY).toContain("applyOperatorHold(decideHealthWrite({");
    const heldBranch = RELAY.indexOf("if (held) {");
    expect(heldBranch).toBeGreaterThan(-1);
    expect(heldBranch).toBeLessThan(RELAY.indexOf("if (disconnected) {"));
    expect(heldBranch).toBeLessThan(RELAY.indexOf('} else if (action === "escalate") {'));
  });

  it("a 423 from /ib/restart is treated as the hold, not retried", () => {
    expect(RELAY).toMatch(/isOperatorHoldRefusal\(res\.status, body\)/);
  });
});
