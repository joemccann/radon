/**
 * @vitest-environment jsdom
 *
 * Route changes are UI changes — the realtime prices socket survives them.
 *
 * App Router navigations REMOUNT each page's WorkspaceShell (pages render the
 * shell; only layouts persist). While the shell owned the usePrices socket,
 * every mobile tab-bar navigation closed the socket, fetched a fresh
 * ws-ticket, re-ran reconnect, resubscribed everything and resynced the
 * snapshot — the 2026-09-01 "every page change lags" bug.
 *
 * The socket now lives in RealtimePricesProvider inside the root Providers
 * tree for the life of the tab. Page shells only PUBLISH what to stream.
 * These tests simulate the App Router page swap (keyed unmount+mount of the
 * publishing consumer) and pin, at the wire:
 *   - one WebSocket construction and ONE /api/ib/ws-ticket fetch across
 *     navigation,
 *   - no unsubscribe/resubscribe churn while a remounting shell's
 *     portfolio/orders inputs re-resolve (shrink linger),
 *   - diff-only subscription changes on the same socket,
 *   - genuine drops still reconnect with backoff and a fresh ticket.
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
import type { OptionContract } from "@/lib/pricesProtocol";

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
}

let wsInstances: MockWebSocket[] = [];
let ticketFetches = 0;

function latestWs(): MockWebSocket { return wsInstances[wsInstances.length - 1]; }
function sentMessages(ws: MockWebSocket): Array<Record<string, unknown>> {
  return ws.sent.map((s) => JSON.parse(s));
}

// usePrices constructs its socket on a microtask (awaits the ws-ticket auth
// URL). Drain a few ticks inside act so the socket exists before assertions.
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

const PLTR_CALL: OptionContract = { symbol: "PLTR", expiry: "20260417", strike: 90, right: "C" };

function requestOf(symbols: string[], contracts: OptionContract[] = []): RealtimeSubscriptionRequest {
  return { symbols, contracts, indexes: [], depthSymbol: null, depthSymbols: [], depthExpiry: null };
}

/** Stand-in for a page's WorkspaceShell: publishes its desired subscriptions.
 *  No cleanup on unmount — exactly like the shell, last-write-wins. */
function PageShell({ request }: { request: RealtimeSubscriptionRequest }) {
  const { publishSubscriptions } = useRealtimePrices();
  useEffect(() => {
    publishSubscriptions(request);
  }, [publishSubscriptions, request]);
  return null;
}

beforeEach(() => {
  wsInstances = [];
  ticketFetches = 0;
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", class extends MockWebSocket {
    constructor(url: string) { super(url); wsInstances.push(this); }
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/ib/ws-ticket")) {
      ticketFetches += 1;
      return new Response(JSON.stringify({ ticket: `ticket-${ticketFetches}` }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
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

describe("RealtimePricesProvider — connection survives page navigation", () => {
  it("remounting the page shell reuses the open socket: no new WebSocket, no new ws-ticket, no resubscribe", async () => {
    const request = requestOf(["PLTR"], [PLTR_CALL]);
    const view = render(
      <RealtimePricesProvider>
        <PageShell key="portfolio" request={request} />
      </RealtimePricesProvider>,
    );
    await flush();
    expect(wsInstances).toHaveLength(1);
    expect(ticketFetches).toBe(1);
    const ws = latestWs();
    act(() => ws.simulateOpen());
    expect(sentMessages(ws).filter((m) => m.action === "subscribe")).toHaveLength(1);
    const sentBeforeNav = ws.sent.length;

    // App Router page swap: the old page's shell unmounts, the new page's
    // shell mounts (key change forces a genuine unmount+mount).
    view.rerender(
      <RealtimePricesProvider>
        <PageShell key="orders" request={requestOf(["PLTR"], [PLTR_CALL])} />
      </RealtimePricesProvider>,
    );
    await flush();
    await advance(SUBSCRIPTION_SHRINK_LINGER_MS + 1_000);

    expect(wsInstances).toHaveLength(1);
    expect(ticketFetches).toBe(1);
    expect(ws.readyState).toBe(MockWebSocket.OPEN);
    expect(ws.sent.length).toBe(sentBeforeNav);
  });

  it("content-identical republishes with fresh array identities are no-ops (no provider render loop)", async () => {
    const view = render(
      <RealtimePricesProvider>
        <PageShell request={requestOf(["PLTR"], [PLTR_CALL])} />
      </RealtimePricesProvider>,
    );
    await flush();
    const ws = latestWs();
    act(() => ws.simulateOpen());
    const sentBefore = ws.sent.length;

    // Publisher memo chains hand fresh identities on every re-render; the
    // provider must diff by content or its own setState re-renders the
    // publisher and loops forever (found while wiring the wire test).
    for (let i = 0; i < 3; i += 1) {
      view.rerender(
        <RealtimePricesProvider>
          <PageShell request={requestOf(["PLTR"], [{ ...PLTR_CALL }])} />
        </RealtimePricesProvider>,
      );
      await flush();
    }

    expect(wsInstances).toHaveLength(1);
    expect(ticketFetches).toBe(1);
    expect(ws.sent.length).toBe(sentBefore);
  });

  it("the remount gap where portfolio/orders re-resolve does not unsubscribe held symbols", async () => {
    const view = render(
      <RealtimePricesProvider>
        <PageShell key="portfolio" request={requestOf(["PLTR", "MSFT"])} />
      </RealtimePricesProvider>,
    );
    await flush();
    const ws = latestWs();
    act(() => ws.simulateOpen());

    // New page's shell mounts before its portfolio GET resolves: it first
    // publishes a REDUCED set, then the full set again ~a second later.
    view.rerender(
      <RealtimePricesProvider>
        <PageShell key="orders" request={requestOf([])} />
      </RealtimePricesProvider>,
    );
    await flush();
    await advance(1_000);
    view.rerender(
      <RealtimePricesProvider>
        <PageShell key="orders" request={requestOf(["PLTR", "MSFT"])} />
      </RealtimePricesProvider>,
    );
    await flush();
    await advance(SUBSCRIPTION_SHRINK_LINGER_MS + 1_000);

    expect(wsInstances).toHaveLength(1);
    expect(sentMessages(ws).filter((m) => m.action === "unsubscribe")).toHaveLength(0);
    // One initial subscribe; the flap must not have re-sent anything.
    expect(sentMessages(ws).filter((m) => m.action === "subscribe")).toHaveLength(1);
  });

  it("a real subscription change after navigation sends only the diff over the same socket", async () => {
    const view = render(
      <RealtimePricesProvider>
        <PageShell key="portfolio" request={requestOf(["PLTR"])} />
      </RealtimePricesProvider>,
    );
    await flush();
    const ws = latestWs();
    act(() => ws.simulateOpen());

    view.rerender(
      <RealtimePricesProvider>
        <PageShell key="ticker" request={requestOf(["PLTR", "NVDA"])} />
      </RealtimePricesProvider>,
    );
    await flush();

    expect(wsInstances).toHaveLength(1);
    const subs = sentMessages(ws).filter((m) => m.action === "subscribe");
    expect(subs).toHaveLength(2);
    expect(subs[1].symbols).toEqual(["NVDA"]);
  });

  it("a genuine shrink commits after the linger window", async () => {
    const view = render(
      <RealtimePricesProvider>
        <PageShell key="ticker" request={requestOf(["PLTR", "NVDA"])} />
      </RealtimePricesProvider>,
    );
    await flush();
    const ws = latestWs();
    act(() => ws.simulateOpen());

    view.rerender(
      <RealtimePricesProvider>
        <PageShell key="portfolio" request={requestOf(["PLTR"])} />
      </RealtimePricesProvider>,
    );
    await flush();
    // Before the linger elapses nothing is unsubscribed…
    expect(sentMessages(ws).filter((m) => m.action === "unsubscribe")).toHaveLength(0);
    await advance(SUBSCRIPTION_SHRINK_LINGER_MS + 1_000);
    // …after it, the stale symbol is released on the SAME socket.
    const unsubs = sentMessages(ws).filter((m) => m.action === "unsubscribe");
    expect(unsubs).toHaveLength(1);
    expect(unsubs[0].symbols).toEqual(["NVDA"]);
    expect(wsInstances).toHaveLength(1);
  });

  it("a genuine drop still reconnects with backoff and a fresh ticket", async () => {
    render(
      <RealtimePricesProvider>
        <PageShell key="portfolio" request={requestOf(["PLTR"])} />
      </RealtimePricesProvider>,
    );
    await flush();
    act(() => latestWs().simulateOpen());
    expect(ticketFetches).toBe(1);

    act(() => latestWs().close());
    await advance(1_600);

    expect(wsInstances).toHaveLength(2);
    expect(ticketFetches).toBe(2);
  });
});
