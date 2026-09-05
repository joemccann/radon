/**
 * @vitest-environment jsdom
 *
 * REL-246 (R-654, R-663).
 * R-654: the socket effect was keyed on `getToken` identity, which the auth
 * provider re-creates every render. Auth churn tore the socket down and
 * reopened it with attempt=0, defeating reconnect backoff.
 * R-663: the demo poll re-armed at the fixed cadence forever on failure;
 * repeated failures must decay the polling interval.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEMO_HEADLINES_POLL_BACKOFF_MAX_MS,
  DEMO_HEADLINES_POLL_MS,
} from "../lib/demo/headlinesPolicy";

const auth = vi.hoisted(() => ({
  // A fresh function identity on every render, like ClerkRealtimeAuthProvider.
  useRealtimeAuth: vi.fn(() => async () => "token"),
}));

vi.mock("../lib/RealtimeAuthContext", () => ({
  useRealtimeAuth: auth.useRealtimeAuth,
}));

vi.mock("../lib/headlinesSocket", () => ({
  buildHeadlinesWebSocketUrl: async () => "ws://localhost:8766/ws-headlines?ticket=t",
  headlinesUrlLeaksUpstream: () => false,
}));

class MockWebSocket {
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
    this.readyState = 3;
    this.onclose?.();
  }
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("REL-246 R-654: auth identity churn", () => {
  it("keeps one socket across provider re-renders that change getToken identity", async () => {
    const { useHeadlines } = await import("../lib/useHeadlines");
    const rendered = renderHook(() => useHeadlines());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(MockWebSocket.instances).toHaveLength(1);

    for (let i = 0; i < 5; i += 1) {
      rendered.rerender();
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
    }

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0]?.readyState).not.toBe(3);
    rendered.unmount();
  });
});

describe("REL-246 R-663: demo poll failure backoff", () => {
  it("decays the polling interval on repeated failures and resets on success", async () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);
    const { useHeadlines } = await import("../lib/useHeadlines");
    const rendered = renderHook(() => useHeadlines());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // First failure: base cadence is no longer enough.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEMO_HEADLINES_POLL_MS);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEMO_HEADLINES_POLL_MS);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    // Second failure: interval decays again (4x base).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2 * DEMO_HEADLINES_POLL_MS);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2 * DEMO_HEADLINES_POLL_MS);
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);

    // A success resets the cadence to the base interval.
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ items: [] }) });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEMO_HEADLINES_POLL_BACKOFF_MAX_MS);
    });
    const afterSuccess = fetchMock.mock.calls.length;
    fetchMock.mockRejectedValue(new Error("offline"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEMO_HEADLINES_POLL_MS);
    });
    expect(fetchMock.mock.calls.length).toBeGreaterThan(afterSuccess);

    rendered.unmount();
  });

  it("caps the decayed interval at the backoff ceiling", async () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);
    const { useHeadlines } = await import("../lib/useHeadlines");
    const rendered = renderHook(() => useHeadlines());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    // Burn through many failures; each ceiling-length advance must still fire.
    for (let i = 0; i < 12; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(DEMO_HEADLINES_POLL_BACKOFF_MAX_MS);
      });
    }
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(10);
    rendered.unmount();
  });
});
