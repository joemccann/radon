/**
 * @vitest-environment jsdom
 *
 * Audit §3.5 "IB status chip" (implicit-state-machines-20261103):
 *  - ibConnected was initialised `true` (fail open) before the relay said anything;
 *  - backend service_state `restarting` / `stopped` / `unreachable` and auth_state
 *    `remote` fell through to the relay flag and could read "connected";
 *  - the legacy `connectionState` was derived separately and disagreed with
 *    `displayStatus` (e.g. awaiting_2fa vs "connected");
 *  - market-data degradation survived a socket drop.
 * Vocabulary: scripts/api/ib_gateway.py (_check_docker / _check_cloud /
 * check_ib_gateway).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import React, { type ReactNode } from "react";
import {
  IBStatusProvider,
  useIBStatusContext,
  deriveDisplayStatus,
  ibRelayTransition,
  legacyConnectionState,
  type IBRelayState,
} from "../lib/IBStatusContext";

class MockWebSocket {
  static CONNECTING = 0 as const;
  static OPEN = 1 as const;
  static CLOSING = 2 as const;
  static CLOSED = 3 as const;
  readyState: number = MockWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  sent: string[] = [];
  constructor(public url: string) {}
  send(data: string) { this.sent.push(data); }
  close() {
    if (this.readyState === MockWebSocket.CLOSED) return;
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new Event("close"));
  }
  simulateOpen() { this.readyState = MockWebSocket.OPEN; this.onopen?.(new Event("open")); }
  simulateMessage(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }); }
  simulateClose() { this.readyState = MockWebSocket.CLOSED; this.onclose?.(new Event("close")); }
}

let wsInstances: MockWebSocket[] = [];
const latestWs = () => wsInstances[wsInstances.length - 1];
async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}
const wrapper = ({ children }: { children: ReactNode }) =>
  React.createElement(IBStatusProvider, null, children);

function healthResponse(auth: string, service: string) {
  return new Response(JSON.stringify({
    ib_gateway: { auth_state: auth, service_state: service, upstream_dead: false },
  }), { status: 200, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  wsInstances = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", class extends MockWebSocket {
    constructor(url: string) { super(url); wsInstances.push(this); }
  });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("IBStatusProvider fails closed", () => {
  it("does not report the broker connected before the relay's first status frame", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("no health")));
    const { result } = renderHook(() => useIBStatusContext(), { wrapper });
    await flush();
    act(() => latestWs().simulateOpen());
    expect(result.current.wsConnected).toBe(true);
    expect(result.current.ibConnected).toBe(false);
    expect(result.current.displayStatus).not.toBe("connected");
    expect(result.current.connectionState).not.toBe("connected");
  });

  it("drops market-data degradation with the socket that reported it", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => healthResponse("authenticated", "healthy")));
    const { result } = renderHook(() => useIBStatusContext(), { wrapper });
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateMessage({ type: "status", ib_connected: true, market_data_degraded: true }));
    expect(result.current.displayStatus).toBe("unhealthy");

    act(() => latestWs().simulateClose());
    await act(async () => {
      vi.advanceTimersByTime(2_000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(wsInstances.length).toBe(2);
    act(() => latestWs().simulateOpen());
    // New relay session, /health authoritative and healthy, no degraded report yet.
    expect(result.current.displayStatus).toBe("connected");
  });
});

describe("deriveDisplayStatus vocabulary", () => {
  const open = (broker: "unknown" | "connected" | "disconnected", marketDataDegraded = false): IBRelayState =>
    ({ phase: "open", broker, marketDataDegraded, disconnectedSince: null });
  const base = { upstreamDead: false as boolean | null };

  it("relay down is relay_offline regardless of health", () => {
    expect(deriveDisplayStatus({ ...base, relay: { phase: "down", disconnectedSince: null }, authState: "authenticated", serviceState: "healthy" })).toBe("relay_offline");
  });

  it("a restarting or starting gateway is never connected", () => {
    for (const serviceState of ["restarting", "starting"] as const) {
      expect(deriveDisplayStatus({ ...base, relay: open("connected"), authState: "authenticated", serviceState })).toBe("unhealthy");
    }
  });

  it("a stopped gateway is offline even when the relay still says connected", () => {
    expect(deriveDisplayStatus({ ...base, relay: open("connected"), authState: "authenticated", serviceState: "stopped" })).toBe("ib_offline");
  });

  it("cloud service_state unreachable is unreachable", () => {
    expect(deriveDisplayStatus({ ...base, relay: open("connected"), authState: "authenticated", serviceState: "unreachable" })).toBe("unreachable");
  });

  it("cloud reachable + authenticated is connected", () => {
    expect(deriveDisplayStatus({ ...base, relay: open("unknown"), authState: "authenticated", serviceState: "reachable" })).toBe("connected");
  });

  it("auth_state remote defers to the relay broker report, unknown is not connected", () => {
    expect(deriveDisplayStatus({ ...base, relay: open("connected"), authState: "remote", serviceState: "reachable" })).toBe("connected");
    expect(deriveDisplayStatus({ ...base, relay: open("disconnected"), authState: "remote", serviceState: "reachable" })).toBe("ib_offline");
    expect(deriveDisplayStatus({ ...base, relay: open("unknown"), authState: "remote", serviceState: "reachable" })).toBe("unhealthy");
  });

  it("no health yet: unknown broker is not connected", () => {
    expect(deriveDisplayStatus({ ...base, relay: open("unknown"), authState: null, serviceState: null, upstreamDead: null })).toBe("unhealthy");
    expect(deriveDisplayStatus({ ...base, relay: open("connected"), authState: null, serviceState: null, upstreamDead: null })).toBe("connected");
  });
});

describe("legacy connectionState derives from displayStatus", () => {
  it("never says connected while the chip says otherwise", () => {
    expect(legacyConnectionState("connected")).toBe("connected");
    expect(legacyConnectionState("relay_offline")).toBe("relay_offline");
    for (const s of ["awaiting_2fa", "unhealthy", "unreachable", "ib_offline"] as const) {
      expect(legacyConnectionState(s)).toBe("ib_offline");
    }
  });

  it("provider: awaiting_2fa with a relay that still says connected is not legacy-connected", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => healthResponse("awaiting_2fa", "healthy")));
    const { result } = renderHook(() => useIBStatusContext(), { wrapper });
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateMessage({ type: "status", ib_connected: true }));
    expect(result.current.displayStatus).toBe("awaiting_2fa");
    expect(result.current.connectionState).toBe("ib_offline");
  });
});

describe("ibRelayTransition", () => {
  it("open starts with an unknown broker and close discards it", () => {
    let s: IBRelayState = { phase: "down", disconnectedSince: null };
    s = ibRelayTransition(s, { type: "open" });
    expect(s).toEqual({ phase: "open", broker: "unknown", marketDataDegraded: false, disconnectedSince: null });
    s = ibRelayTransition(s, { type: "status", ibConnected: true, marketDataDegraded: true, at: 5 });
    expect(s).toEqual({ phase: "open", broker: "connected", marketDataDegraded: true, disconnectedSince: null });
    s = ibRelayTransition(s, { type: "down", at: 9 });
    expect(s).toEqual({ phase: "down", disconnectedSince: 9 });
    s = ibRelayTransition(s, { type: "open" });
    expect(s).toEqual({ phase: "open", broker: "unknown", marketDataDegraded: false, disconnectedSince: 9 });
  });

  it("keeps the first disconnect timestamp and clears it on a connected report", () => {
    let s: IBRelayState = { phase: "open", broker: "unknown", marketDataDegraded: false, disconnectedSince: null };
    s = ibRelayTransition(s, { type: "status", ibConnected: false, marketDataDegraded: false, at: 1 });
    s = ibRelayTransition(s, { type: "status", ibConnected: false, marketDataDegraded: false, at: 2 });
    expect(s).toMatchObject({ broker: "disconnected", disconnectedSince: 1 });
    s = ibRelayTransition(s, { type: "status", ibConnected: true, marketDataDegraded: false, at: 3 });
    expect(s).toMatchObject({ broker: "connected", disconnectedSince: null });
  });

  it("ignores a status frame while down", () => {
    const down: IBRelayState = { phase: "down", disconnectedSince: 4 };
    expect(ibRelayTransition(down, { type: "status", ibConnected: true, marketDataDegraded: false, at: 5 })).toBe(down);
  });
});
