/**
 * @vitest-environment jsdom
 *
 * Unit tests for IBStatusContext — shared IB connection status via React Context.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import React from "react";
import { IBStatusProvider, useIBStatusContext, parseIbHealth } from "../lib/IBStatusContext";
import type { ReactNode } from "react";

describe("parseIbHealth — normalises both health sources", () => {
  it("reads /edge-health/status when radon-api probe is up", () => {
    const payload = {
      probes: {
        "radon-api": {
          state: "up",
          payload: { auth_state: "authenticated", service_state: "healthy", upstream_dead: false },
        },
        "radon-relay": { state: "up" },
      },
    };
    expect(parseIbHealth(payload)).toEqual({
      authState: "authenticated",
      serviceState: "healthy",
      upstreamDead: false,
    });
  });

  it("returns unreachable when the edge daemon reports radon-api down", () => {
    const payload = { probes: { "radon-api": { state: "down", detail: "ConnectionRefusedError" } } };
    expect(parseIbHealth(payload)).toEqual({
      authState: "unreachable",
      serviceState: null,
      upstreamDead: null,
    });
  });

  it("returns null (caller falls back) when the radon-api probe is indeterminate", () => {
    const payload = { probes: { "radon-api": { state: "unknown", detail: "http timeout" } } };
    expect(parseIbHealth(payload)).toBeNull();
  });

  it("reads the flat /api/admin/health shape", () => {
    const payload = { ib_gateway: { auth_state: "awaiting_2fa", service_state: "healthy", upstream_dead: false } };
    expect(parseIbHealth(payload)).toEqual({
      authState: "awaiting_2fa",
      serviceState: "healthy",
      upstreamDead: false,
    });
  });

  it("returns null for empty / unrecognised payloads", () => {
    expect(parseIbHealth(null)).toBeNull();
    expect(parseIbHealth(undefined)).toBeNull();
    expect(parseIbHealth({})).toBeNull();
  });
});

/* ---------- MockWebSocket ---------- */
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
  url: string;
  constructor(url: string) { this.url = url; }
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
function latestWs(): MockWebSocket { return wsInstances[wsInstances.length - 1]; }

async function flushSocketOpen() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

const wrapper = ({ children }: { children: ReactNode }) =>
  React.createElement(IBStatusProvider, null, children);

beforeEach(() => {
  wsInstances = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", class extends MockWebSocket {
    constructor(url: string) { super(url); wsInstances.push(this); }
  });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("IBStatusProvider", () => {
  it("renders children", () => {
    const { result } = renderHook(() => useIBStatusContext(), { wrapper });
    expect(result.current).toBeDefined();
    expect(result.current.wsConnected).toBe(false);
  });

  it("multiple consumers share same connection (only 1 WebSocket created)", async () => {
    const Wrapper = ({ children }: { children: ReactNode }) =>
      React.createElement(IBStatusProvider, null, children);

    // Render two hooks under the same provider
    const { result: r1 } = renderHook(() => useIBStatusContext(), { wrapper: Wrapper });
    await flushSocketOpen();
    // Even with a second consumer, still only 1 WebSocket
    expect(wsInstances).toHaveLength(1);
    expect(r1.current).toBeDefined();
  });

  it("wsConnected updates on WS open/close", async () => {
    const { result } = renderHook(() => useIBStatusContext(), { wrapper });
    await flushSocketOpen();
    expect(result.current.wsConnected).toBe(false);
    act(() => latestWs().simulateOpen());
    expect(result.current.wsConnected).toBe(true);
    act(() => latestWs().simulateClose());
    expect(result.current.wsConnected).toBe(false);
  });

  it("ibConnected updates from status message", async () => {
    const { result } = renderHook(() => useIBStatusContext(), { wrapper });
    await flushSocketOpen();
    act(() => latestWs().simulateOpen());
    // Default ibConnected is true (assume connected until told otherwise)
    act(() => latestWs().simulateMessage({ type: "status", ib_connected: false }));
    expect(result.current.ibConnected).toBe(false);
    act(() => latestWs().simulateMessage({ type: "status", ib_connected: true }));
    expect(result.current.ibConnected).toBe(true);
  });

  it("disconnectedSince set when IB disconnects, cleared on reconnect", async () => {
    const { result } = renderHook(() => useIBStatusContext(), { wrapper });
    await flushSocketOpen();
    act(() => latestWs().simulateOpen());
    expect(result.current.disconnectedSince).toBeNull();
    act(() => latestWs().simulateMessage({ type: "status", ib_connected: false }));
    expect(result.current.disconnectedSince).toBeTypeOf("number");
    const ts = result.current.disconnectedSince;
    // Sending another disconnect should not change the timestamp
    act(() => latestWs().simulateMessage({ type: "status", ib_connected: false }));
    expect(result.current.disconnectedSince).toBe(ts);
    // Reconnect clears it
    act(() => latestWs().simulateMessage({ type: "status", ib_connected: true }));
    expect(result.current.disconnectedSince).toBeNull();
  });

  it("connectionState derived correctly for all 3 states", async () => {
    const { result } = renderHook(() => useIBStatusContext(), { wrapper });
    await flushSocketOpen();
    // Initial: relay offline (WS not connected)
    expect(result.current.connectionState).toBe("relay_offline");

    act(() => latestWs().simulateOpen());
    // WS open + ibConnected default true = connected
    expect(result.current.connectionState).toBe("connected");

    act(() => latestWs().simulateMessage({ type: "status", ib_connected: false }));
    // WS open + IB disconnected = ib_offline
    expect(result.current.connectionState).toBe("ib_offline");

    act(() => latestWs().simulateClose());
    // WS closed = relay_offline
    expect(result.current.connectionState).toBe("relay_offline");
  });

  it("responds to ping with pong", async () => {
    const { result } = renderHook(() => useIBStatusContext(), { wrapper });
    await flushSocketOpen();
    act(() => latestWs().simulateOpen());
    const ws = latestWs();
    act(() => ws.simulateMessage({ type: "ping" }));
    const pongMsg = ws.sent.find(s => {
      try { return JSON.parse(s).action === "pong"; } catch { return false; }
    });
    expect(pongMsg).toBeDefined();
  });

  it("reconnects on WS close with backoff", async () => {
    const { result } = renderHook(() => useIBStatusContext(), { wrapper });
    await flushSocketOpen();
    expect(wsInstances).toHaveLength(1);
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateClose());
    // Should schedule reconnect
    const beforeCount = wsInstances.length;
    await act(async () => {
      vi.advanceTimersByTime(2000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(wsInstances.length).toBeGreaterThan(beforeCount);
  });

  it("closes a status socket that never reaches open", async () => {
    renderHook(() => useIBStatusContext(), { wrapper });
    await flushSocketOpen();
    const stalled = latestWs();

    await act(async () => {
      vi.advanceTimersByTime(10_001);
      await Promise.resolve();
    });

    expect(stalled.readyState).toBe(MockWebSocket.CLOSED);
  });

  it("bounds authoritative health polling with an abort signal", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        ib_gateway: {
          auth_state: "authenticated",
          service_state: "healthy",
          upstream_dead: false,
        },
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useIBStatusContext(), { wrapper });
    await flushSocketOpen();

    expect(fetchMock).toHaveBeenCalled();
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(options.signal).toBeInstanceOf(AbortSignal);
  });

  it("stops reporting connected after authoritative health stays unavailable", async () => {
    const healthy = new Response(JSON.stringify({
      ib_gateway: {
        auth_state: "authenticated",
        service_state: "healthy",
        upstream_dead: false,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(healthy)
      .mockRejectedValue(new Error("health endpoint unavailable"));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useIBStatusContext(), { wrapper });
    await flushSocketOpen();
    act(() => latestWs().simulateOpen());
    expect(result.current.displayStatus).toBe("connected");

    for (let poll = 0; poll < 3; poll += 1) {
      await act(async () => {
        vi.advanceTimersByTime(15_000);
        await Promise.resolve();
        await Promise.resolve();
      });
    }

    expect(result.current.displayStatus).toBe("unhealthy");
    expect(result.current.authState).toBe("unknown");
  });

  it("cleans up WebSocket on unmount", async () => {
    const { unmount } = renderHook(() => useIBStatusContext(), { wrapper });
    await flushSocketOpen();
    act(() => latestWs().simulateOpen());
    const ws = latestWs();
    unmount();
    expect(ws.readyState).toBe(MockWebSocket.CLOSED);
  });

  it("sets wsConnected false and disconnectedSince when WS drops", async () => {
    const { result } = renderHook(() => useIBStatusContext(), { wrapper });
    await flushSocketOpen();
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateMessage({ type: "status", ib_connected: true }));
    expect(result.current.wsConnected).toBe(true);
    expect(result.current.disconnectedSince).toBeNull();
    act(() => latestWs().simulateClose());
    expect(result.current.wsConnected).toBe(false);
    expect(result.current.disconnectedSince).toBeTypeOf("number");
  });
});
