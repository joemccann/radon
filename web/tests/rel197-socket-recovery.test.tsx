/**
 * @vitest-environment jsdom
 *
 * REL-197 (R-529, R-563): the tab-lifetime socket recovers from reconnect
 * exhaustion, exhausted marks are never silently live, and the shrink linger
 * converges under churn instead of accumulating an unbounded union.
 *
 * Before 0f7e66bf the 10-attempt reconnect cap was harmless — every page
 * remounted usePrices. Now the ONE instance lives in root Providers for the
 * tab's life: one ~3.2-minute relay outage exhausted the strategy, nothing
 * ever reconnected, and the never-cleared price map rendered last-known
 * marks in every money surface all day.
 */
import React, { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";

vi.mock("@/lib/RealtimeAuthContext", () => ({
  useRealtimeAuth: () => async () => "clerk-token",
}));

import {
  RealtimePricesProvider,
  SUBSCRIPTION_SHRINK_LINGER_MS,
  useRealtimePrices,
  type RealtimeSubscriptionRequest,
} from "@/lib/RealtimePricesContext";

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
  simulateMessage(payload: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

let wsInstances: MockWebSocket[] = [];
const latestWs = () => wsInstances[wsInstances.length - 1];

async function flush() {
  await act(async () => {
    await Promise.resolve();
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

function requestOf(symbols: string[]): RealtimeSubscriptionRequest {
  return { symbols, contracts: [], indexes: [], depthSymbol: null, depthSymbols: [], depthExpiry: null };
}

let observed: { prices: Record<string, unknown>; connected: boolean; error: string | null } = {
  prices: {},
  connected: false,
  error: null,
};

function Probe({ request }: { request: RealtimeSubscriptionRequest }) {
  const realtime = useRealtimePrices();
  useEffect(() => {
    realtime.publishSubscriptions(request);
  }, [realtime.publishSubscriptions, request]);
  observed = { prices: realtime.prices, connected: realtime.connected, error: realtime.error };
  return null;
}

/** Fail every reconnect attempt until the strategy is exhausted. */
async function exhaustReconnects() {
  for (let i = 0; i < 12; i += 1) {
    await advance(35_000); // past the 30s backoff cap + jitter
    await flush();
    const ws = latestWs();
    if (ws.readyState !== MockWebSocket.CLOSED) {
      act(() => { ws.close(); });
    }
  }
}

beforeEach(() => {
  wsInstances = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", class extends MockWebSocket {
    constructor(url: string) { super(url); wsInstances.push(this); }
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/api/ib/ws-ticket")) {
      return new Response(JSON.stringify({ ticket: "t" }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("REL-197 — exhausted socket recovers", () => {
  it("a publish that changes the desired set after exhaustion attempts a connect", async () => {
    const { rerender } = render(
      <RealtimePricesProvider><Probe request={requestOf(["SPY"])} /></RealtimePricesProvider>,
    );
    await flush();
    act(() => { latestWs().simulateOpen(); });
    act(() => { latestWs().close(); });
    await exhaustReconnects();
    const countAfterExhaustion = wsInstances.length;
    await advance(60_000);
    expect(wsInstances.length).toBe(countAfterExhaustion); // truly exhausted

    rerender(
      <RealtimePricesProvider><Probe request={requestOf(["SPY", "QQQ"])} /></RealtimePricesProvider>,
    );
    await flush();
    await advance(2_000);
    expect(wsInstances.length).toBeGreaterThan(countAfterExhaustion);
  });

  it("visibilitychange to visible after exhaustion attempts a connect", async () => {
    render(
      <RealtimePricesProvider><Probe request={requestOf(["SPY"])} /></RealtimePricesProvider>,
    );
    await flush();
    act(() => { latestWs().simulateOpen(); });
    act(() => { latestWs().close(); });
    await exhaustReconnects();
    const countAfterExhaustion = wsInstances.length;

    Object.defineProperty(document, "visibilityState", {
      configurable: true, get: () => "visible",
    });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    await advance(2_000);
    expect(wsInstances.length).toBeGreaterThan(countAfterExhaustion);
  });

  it("exhaustion does not leave last-known marks rendering as live", async () => {
    render(
      <RealtimePricesProvider><Probe request={requestOf(["SPY"])} /></RealtimePricesProvider>,
    );
    await flush();
    act(() => { latestWs().simulateOpen(); });
    act(() => {
      latestWs().simulateMessage({
        type: "price", data: { symbol: "SPY", last: 650.12, close: 648.0 },
      });
    });
    await flush();
    expect(Object.keys(observed.prices)).toContain("SPY");

    act(() => { latestWs().close(); });
    await exhaustReconnects();
    await flush();
    expect(Object.keys(observed.prices)).toHaveLength(0);
  });
});

describe("REL-197 — shrink linger converges under churn (R-563)", () => {
  it("churn faster than the linger converges to the desired set within one window", async () => {
    const { rerender } = render(
      <RealtimePricesProvider><Probe request={requestOf(["AAA"])} /></RealtimePricesProvider>,
    );
    await flush();
    act(() => { latestWs().simulateOpen(); });
    await flush();
    const ws = latestWs();

    // Churn through pages faster than the linger window.
    for (const syms of [["BBB"], ["CCC"], ["DDD"], ["EEE"]]) {
      rerender(
        <RealtimePricesProvider><Probe request={requestOf(syms)} /></RealtimePricesProvider>,
      );
      await flush();
      await advance(Math.floor(SUBSCRIPTION_SHRINK_LINGER_MS / 3));
    }
    // Mid-churn, before any settle: the applied set must stay bounded by
    // (previous desired ∪ current desired), never the accumulated union.
    const midChurn = new Set<string>();
    for (const raw of ws.sent) {
      const message = JSON.parse(raw) as { action?: string; symbols?: string[] };
      if (message.action === "subscribe") message.symbols?.forEach((s) => midChurn.add(s));
      if (message.action === "unsubscribe") message.symbols?.forEach((s) => midChurn.delete(s));
    }
    expect(midChurn.size).toBeLessThanOrEqual(2);

    await advance(SUBSCRIPTION_SHRINK_LINGER_MS + 1_000);
    await flush();

    const subscribed = new Set<string>();
    for (const raw of ws.sent) {
      const message = JSON.parse(raw) as { action?: string; symbols?: string[] };
      if (message.action === "subscribe") message.symbols?.forEach((s) => subscribed.add(s));
      if (message.action === "unsubscribe") message.symbols?.forEach((s) => subscribed.delete(s));
    }
    expect([...subscribed].sort()).toEqual(["EEE"]);
  });
});
