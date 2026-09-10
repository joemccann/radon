/**
 * @vitest-environment jsdom
 *
 * REL-163 (R-460, hook half): `useHeadlines.open()` checked `stopped` only
 * BEFORE `await buildHeadlinesWebSocketUrl(...)`. Cleanup during the await
 * found `socket === null`, the socket was then constructed after unmount, and
 * its `onclose` returned early, so nothing ever closed it. React StrictMode
 * hits this deterministically and each leak pins one of the hub's 32 slots.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tickets = vi.hoisted(() => {
  const waiting: Array<(url: string) => void> = [];
  return {
    issue: () => new Promise<string>((resolve) => waiting.push(resolve)),
    resolve: (url: string) => waiting.shift()?.(url),
  };
});

vi.mock("../lib/headlinesSocket", () => ({
  buildHeadlinesWebSocketUrl: () => tickets.issue(),
  headlinesUrlLeaksUpstream: () => false,
}));

import { useHeadlines } from "../lib/useHeadlines";

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
  vi.unstubAllGlobals();
});

describe("useHeadlines unmount during the awaited ticket", () => {
  it("leaves no open socket behind", async () => {
    const { unmount } = renderHook(() => useHeadlines());
    await act(async () => {
      await Promise.resolve();
    });
    expect(MockWebSocket.instances).toHaveLength(0);
    unmount();
    await act(async () => {
      tickets.resolve("ws://localhost:8766/ws-headlines?ticket=t");
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    // T-366: the fixed hook must never reach the constructor after unmount
    // (R-460 checks `stopped` again after the awaited ticket), so zero
    // instances — not merely zero OPEN instances — is the contract.
    expect(MockWebSocket.instances).toHaveLength(0);
    const leaked = MockWebSocket.instances.filter((socket) => socket.readyState !== 3);
    expect(leaked).toHaveLength(0);
  });

  // T-366: drain-schedule control. The unmount test proves the fix by the
  // ABSENCE of a socket, which is vacuously green if open() gains one more
  // await and never reaches the WebSocket constructor inside the drained
  // ticks. Same resolve+drain schedule without unmount must construct the
  // socket on the first tick, so an extra await in the open path turns THIS
  // red instead of leaving both tests green without executing the fix.
  it("constructs the socket on the same drain schedule when not unmounted", async () => {
    const { unmount } = renderHook(() => useHeadlines());
    await act(async () => {
      await Promise.resolve();
    });
    expect(MockWebSocket.instances).toHaveLength(0);
    await act(async () => {
      tickets.resolve("ws://localhost:8766/ws-headlines?ticket=t");
      await Promise.resolve();
      expect(MockWebSocket.instances).toHaveLength(1);
    });
    unmount();
    expect(MockWebSocket.instances[0]?.readyState).toBe(3);
  });
});
