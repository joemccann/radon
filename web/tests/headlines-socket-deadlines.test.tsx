/**
 * @vitest-environment jsdom
 *
 * Audit 2026-11-03 §3.5 "Headlines socket": no open timeout and no stale
 * watchdog, so a socket stuck in CONNECTING, or one that opened but never
 * delivered the hub's admit snapshot (half-open), sat on "connecting" /
 * stale data forever and never retried. usePrices already bounds both.
 *
 * The hub (scripts/mktnews/hub.js) always sends a snapshot frame on admit and
 * otherwise only speaks when news arrives, so the watchdog bounds the FIRST
 * frame; a quiet but admitted socket must survive.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { REALTIME_OPEN_TIMEOUT_MS } from "../lib/realtimeDeadline";

vi.mock("../lib/RealtimeAuthContext", () => ({
  useRealtimeAuth: () => async () => "token",
}));
vi.mock("../lib/headlinesSocket", () => ({
  buildHeadlinesWebSocketUrl: async () => "ws://localhost:8766/ws-headlines?ticket=t",
  headlinesUrlLeaksUpstream: () => false,
}));

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    MockWebSocket.instances.push(this);
  }
  close() {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.onclose?.();
  }
  simulateOpen() {
    this.readyState = 1;
    this.onopen?.();
  }
  simulateMessage(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

const latest = () => MockWebSocket.instances[MockWebSocket.instances.length - 1];

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}
async function advance(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", MockWebSocket);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("headlines socket deadlines", () => {
  it("closes a socket stuck in CONNECTING and retries", async () => {
    const { useHeadlines } = await import("../lib/useHeadlines");
    const { result, unmount } = renderHook(() => useHeadlines());
    await flush();
    const stuck = latest();
    expect(result.current.status).toBe("connecting");

    await advance(REALTIME_OPEN_TIMEOUT_MS + 1);
    expect(stuck.readyState).toBe(3);
    expect(result.current.status).toBe("down");

    await advance(10_000);
    expect(MockWebSocket.instances).toHaveLength(2);
    unmount();
  });

  it("closes an open socket that never delivers the admit snapshot and retries", async () => {
    const { useHeadlines } = await import("../lib/useHeadlines");
    const { unmount } = renderHook(() => useHeadlines());
    await flush();
    const halfOpen = latest();
    act(() => halfOpen.simulateOpen());

    await advance(REALTIME_OPEN_TIMEOUT_MS + 1);
    expect(halfOpen.readyState).toBe(3);
    await advance(10_000);
    expect(MockWebSocket.instances).toHaveLength(2);
    unmount();
  });

  it("keeps an admitted socket open through a quiet news period", async () => {
    const { useHeadlines } = await import("../lib/useHeadlines");
    const { result, unmount } = renderHook(() => useHeadlines());
    await flush();
    const ws = latest();
    act(() => ws.simulateOpen());
    act(() => ws.simulateMessage({ type: "snapshot", items: [] }));
    expect(result.current.status).toBe("live");

    await advance(10 * 60_000);
    expect(ws.readyState).toBe(1);
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(result.current.status).toBe("live");
    unmount();
  });
});
