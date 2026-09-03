/**
 * @vitest-environment jsdom
 *
 * REL-182 (R-513): a flash-REST-fed headline (frame.degraded === true) must
 * not flip the client status to "live" — that cleared the REL-155 down
 * banner on the first fed print while the upstream WS was still down.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

vi.mock("../lib/headlinesSocket", () => ({
  buildHeadlinesWebSocketUrl: async () => "ws://127.0.0.1:9/hub",
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
  frame(payload: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const ITEM = { id: "flash-1", title: "CPI prints hot", time: "2026-09-03T12:00:00Z" };

async function openSocket() {
  const rendered = renderHook(() => useHeadlines());
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  const ws = MockWebSocket.instances.at(-1)!;
  act(() => {
    ws.readyState = 1;
    ws.onopen?.();
  });
  return { rendered, ws };
}

describe("REL-182 — degraded frames do not read as live", () => {
  it("a degraded headline updates the tape but keeps the down status", async () => {
    const { rendered, ws } = await openSocket();
    act(() => {
      ws.frame({ type: "status", state: "upstream-down" });
    });
    expect(rendered.result.current.status).toBe("down");
    act(() => {
      ws.frame({ type: "headline", item: ITEM, degraded: true });
    });
    expect(rendered.result.current.items.some((i) => i.id === "flash-1")).toBe(true);
    expect(rendered.result.current.status).toBe("down");
  });

  it("an ordinary headline still flips to live", async () => {
    const { rendered, ws } = await openSocket();
    act(() => {
      ws.frame({ type: "status", state: "upstream-down" });
    });
    act(() => {
      ws.frame({ type: "headline", item: ITEM });
    });
    expect(rendered.result.current.status).toBe("live");
  });
});
