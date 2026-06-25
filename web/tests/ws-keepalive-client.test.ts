/**
 * @vitest-environment jsdom
 *
 * Unit tests for WebSocket keep-alive (ping/pong) and staleness detection.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import React from "react";
import { usePrices } from "../lib/usePrices";
import { IBStatusProvider, useIBStatusContext } from "../lib/IBStatusContext";
import type { ReactNode } from "react";

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
  closeCalled = false;
  constructor(url: string) { this.url = url; }
  send(data: string) { this.sent.push(data); }
  close() {
    this.closeCalled = true;
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
function sentMessages(ws: MockWebSocket) { return ws.sent.map(s => JSON.parse(s)); }

// usePrices builds its socket on a microtask (awaits the WS-ticket auth URL),
// so drain microtasks after render before reading latestWs().
async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

const ibWrapper = ({ children }: { children: ReactNode }) =>
  React.createElement(IBStatusProvider, null, children);

beforeEach(() => {
  wsInstances = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", class extends MockWebSocket {
    constructor(url: string) { super(url); wsInstances.push(this); }
  });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("usePrices ping/pong", () => {
  it("responds to ping message with pong", async () => {
    renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    const ws = latestWs();
    act(() => ws.simulateOpen());
    act(() => ws.simulateMessage({ type: "ping" }));

    const pongs = sentMessages(ws).filter(m => m.action === "pong");
    expect(pongs).toHaveLength(1);
  });

  it("force-reconnects after 60s of silence", async () => {
    renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    const ws = latestWs();
    act(() => ws.simulateOpen());

    expect(ws.closeCalled).toBe(false);

    // The staleness check runs every 15s.
    // At 75s of silence, Date.now() - lastMessage > 60s, so close is called.
    // First advance just past the threshold + one check cycle
    act(() => vi.advanceTimersByTime(61_000));
    // The check at 75s (5th interval tick) should have closed the stale socket
    act(() => vi.advanceTimersByTime(15_000));

    expect(ws.closeCalled).toBe(true);
    expect(ws.readyState).toBe(MockWebSocket.CLOSED);
  });
});

describe("IBStatusContext ping/pong", () => {
  it("responds to ping message with pong", async () => {
    renderHook(() => useIBStatusContext(), { wrapper: ibWrapper });
    await flush();
    const ws = latestWs();
    act(() => ws.simulateOpen());
    act(() => ws.simulateMessage({ type: "ping" }));

    const pongs = ws.sent.filter(s => {
      try { return JSON.parse(s).action === "pong"; } catch { return false; }
    });
    expect(pongs).toHaveLength(1);
  });

  it("force-reconnects after 60s of silence", async () => {
    renderHook(() => useIBStatusContext(), { wrapper: ibWrapper });
    await flush();
    const ws = latestWs();
    act(() => ws.simulateOpen());

    expect(ws.closeCalled).toBe(false);

    act(() => vi.advanceTimersByTime(61_000));
    act(() => vi.advanceTimersByTime(15_000));

    expect(ws.closeCalled).toBe(true);
    expect(ws.readyState).toBe(MockWebSocket.CLOSED);
  });
});
