/**
 * REL-068 tranche G — R-167, R-168.
 *
 * While `ibConnected === false` during RTH the relay writes NO service_health
 * row at all: decideStaleAction returns "none", hasHealthyDataPlane is false
 * so the heartbeat and clearError are withheld, and isDataPlaneNulled is false
 * so `degraded` is withheld too. A relay whose socket is simply gone during
 * the session is therefore indistinguishable from one that is fine.
 *
 * And the reconnect is a flat 5 s with no backoff, jitter or cap, so a Gateway
 * sitting at 2FA is dialled 720 times an hour for as long as it takes.
 */
import { describe, expect, it } from "vitest";

import {
  decideHealthWrite,
  STALE_DATA_THRESHOLD_MS,
} from "../../scripts/lib/staleDataMachine.js";
import { createReconnectGate, reconnectDelayMs } from "../../scripts/lib/reconnectGate.js";

const NOW = 1_800_000_000_000;

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

describe("R-168: a disconnected relay during RTH says so", () => {
  it("reports disconnected when the socket has been down past the threshold", () => {
    const { disconnected, heartbeat, degraded } = decideHealthWrite(input());
    expect(disconnected).toBe(true);
    expect(heartbeat).toBe(false);
    expect(degraded).toBe(false);
  });

  it("does not fire during an ordinary short reconnect", () => {
    const { disconnected } = decideHealthWrite(
      input({ disconnectedSinceAt: NOW - 1_000 }),
    );
    expect(disconnected).toBe(false);
  });

  it("stays quiet outside market hours", () => {
    const { disconnected } = decideHealthWrite(input({ isMarketHours: false }));
    expect(disconnected).toBe(false);
  });

  it("stays quiet when nobody is subscribed", () => {
    const { disconnected } = decideHealthWrite(
      input({ subscribedSymbols: 0, activeSubscriptions: 0 }),
    );
    expect(disconnected).toBe(false);
  });

  it("does not fire while the socket is up", () => {
    const { disconnected } = decideHealthWrite(
      input({ ibConnected: true, disconnectedSinceAt: null }),
    );
    expect(disconnected).toBe(false);
  });

  it("never coincides with a heartbeat, so the two writes cannot race", () => {
    const healthy = decideHealthWrite(
      input({ ibConnected: true, disconnectedSinceAt: null, lastTickAt: NOW, activeSubscriptions: 12 }),
    );
    expect(healthy.disconnected).toBe(false);
    expect(healthy.heartbeat).toBe(true);
  });

  it("is wired into the relay's stale-check loop", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const src = readFileSync(
      join(__dirname, "..", "..", "scripts", "ib_realtime_server.js"),
      "utf-8",
    );
    expect(src).toContain("disconnected");
    expect(src).toMatch(/disconnectedSinceAt/);
  });
});

describe("R-167: the IB reconnect backs off", () => {
  it("starts at the base delay", () => {
    expect(reconnectDelayMs(0)).toBeGreaterThanOrEqual(5_000);
    expect(reconnectDelayMs(0)).toBeLessThan(7_000);
  });

  it("grows with consecutive failures", () => {
    expect(reconnectDelayMs(3)).toBeGreaterThan(reconnectDelayMs(1));
  });

  it("is capped", () => {
    const capped = reconnectDelayMs(50);
    expect(capped).toBeLessThanOrEqual(120_000);
    expect(reconnectDelayMs(99)).toBeLessThanOrEqual(capped * 1.5);
  });

  it("carries jitter so a fleet does not resonate", () => {
    const samples = new Set(Array.from({ length: 40 }, () => reconnectDelayMs(4)));
    expect(samples.size).toBeGreaterThan(1);
  });

  it("still honours an explicit per-call delay", () => {
    const fired: number[] = [];
    const gate = createReconnectGate({
      delayMs: 5_000,
      setTimer: (_cb: () => void, ms: number) => { fired.push(ms); return 1; },
      clearTimer: () => {},
    });
    gate.schedule(() => {}, 42_000);
    expect(fired).toEqual([42_000]);
  });

  it("the relay schedules with the backoff, not the flat constant", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const src = readFileSync(
      join(__dirname, "..", "..", "scripts", "ib_realtime_server.js"),
      "utf-8",
    );
    const body = src.split("function scheduleReconnect()")[1].split("\nfunction ")[0];
    expect(body).toContain("reconnectDelayMs");
  });

  it("the attempt counter resets once the socket is up", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const src = readFileSync(
      join(__dirname, "..", "..", "scripts", "ib_realtime_server.js"),
      "utf-8",
    );
    expect(src).toMatch(/ibReconnectAttempts = 0/);
  });
});
