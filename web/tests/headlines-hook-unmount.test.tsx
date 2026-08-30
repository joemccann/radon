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

const deferred = vi.hoisted(() => {
  let resolve: (url: string) => void = () => {};
  const promise = new Promise<string>((r) => {
    resolve = r;
  });
  return { promise, resolve: (url: string) => resolve(url) };
});

vi.mock("../lib/headlinesSocket", () => ({
  buildHeadlinesWebSocketUrl: () => deferred.promise,
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
      deferred.resolve("ws://localhost:8766/ws-headlines?ticket=t");
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    const leaked = MockWebSocket.instances.filter((socket) => socket.readyState !== 3);
    expect(leaked).toHaveLength(0);
  });
});
