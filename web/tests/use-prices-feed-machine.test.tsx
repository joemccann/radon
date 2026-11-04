/**
 * @vitest-environment jsdom
 *
 * Audit §1 #15 (implicit-state-machines-20261103): the price socket's broker
 * state must not survive a socket drop. Before the fix `onclose` reset
 * `connected` but kept `ibConnected` / `marketDataDegraded`, so after a
 * reconnect every order owner's `feedConnected = connected && ibConnected &&
 * !marketDataDegraded` re-armed on the PREVIOUS socket's broker report until
 * the new socket's first status frame landed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePrices, priceFeedTransition, derivePriceFeedView, type PriceFeedState } from "../lib/usePrices";
import { useRealtimePrices } from "../lib/RealtimePricesContext";

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
async function advance(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  wsInstances = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", class extends MockWebSocket {
    constructor(url: string) { super(url); wsInstances.push(this); }
  });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

const feedConnected = (r: { connected: boolean; ibConnected: boolean; marketDataDegraded?: boolean }) =>
  r.connected && r.ibConnected && !r.marketDataDegraded;

describe("usePrices broker state across a socket drop", () => {
  it("clears broker liveness when the socket closes", async () => {
    const { result } = renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateMessage({
      type: "status", ib_connected: true, market_data_degraded: true,
      ib_issue: "farm", ib_status_message: "farm down",
    }));
    expect(result.current.ibConnected).toBe(true);

    act(() => latestWs().simulateClose());
    expect(result.current.connected).toBe(false);
    expect(result.current.ibConnected).toBe(false);
    expect(result.current.marketDataDegraded).toBe(false);
    expect(result.current.ibIssue).toBeNull();
    expect(result.current.ibStatusMessage).toBeNull();
  });

  it("keeps the order gate closed after reconnect until the new socket reports the broker", async () => {
    const { result } = renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateMessage({ type: "status", ib_connected: true, market_data_degraded: false }));
    expect(feedConnected(result.current)).toBe(true);

    act(() => latestWs().simulateClose());
    await advance(2_000);
    expect(wsInstances).toHaveLength(2);
    act(() => latestWs().simulateOpen());

    // Socket is open but the new relay session has not said anything yet.
    expect(result.current.connected).toBe(true);
    expect(result.current.ibConnected).toBe(false);
    expect(feedConnected(result.current)).toBe(false);

    act(() => latestWs().simulateMessage({ type: "status", ib_connected: true, market_data_degraded: false }));
    expect(feedConnected(result.current)).toBe(true);
  });
});

describe("priceFeedTransition", () => {
  const reported = {
    type: "status" as const,
    ibConnected: true,
    marketDataDegraded: true,
    ibIssue: "x",
    ibStatusMessage: "y",
  };

  it("walks idle -> connecting -> open -> closed and drops the broker report on close", () => {
    let s: PriceFeedState = { phase: "idle" };
    s = priceFeedTransition(s, { type: "connect" });
    expect(s).toEqual({ phase: "connecting" });
    s = priceFeedTransition(s, { type: "open" });
    expect(s).toEqual({ phase: "open", broker: { kind: "unknown" } });
    s = priceFeedTransition(s, reported);
    expect(derivePriceFeedView(s)).toEqual({
      connected: true, ibConnected: true, marketDataDegraded: true, ibIssue: "x", ibStatusMessage: "y",
    });
    s = priceFeedTransition(s, { type: "closed" });
    expect(s).toEqual({ phase: "closed" });
    expect(derivePriceFeedView(s)).toEqual({
      connected: false, ibConnected: false, marketDataDegraded: false, ibIssue: null, ibStatusMessage: null,
    });
  });

  it("ignores a status frame that arrives while not open", () => {
    for (const phase of ["idle", "connecting", "closed"] as const) {
      const s = { phase } as PriceFeedState;
      expect(priceFeedTransition(s, reported)).toBe(s);
    }
  });

  it("an aborted connect attempt returns to idle instead of sticking in connecting", () => {
    expect(priceFeedTransition({ phase: "connecting" }, { type: "reset" })).toEqual({ phase: "idle" });
  });

  it("open is only reachable from connecting", () => {
    const closed: PriceFeedState = { phase: "closed" };
    expect(priceFeedTransition(closed, { type: "open" })).toBe(closed);
  });
});

describe("RealtimePricesContext default (no provider)", () => {
  it("fails closed on market-data health", () => {
    const { result } = renderHook(() => useRealtimePrices());
    expect(result.current.marketDataDegraded).toBe(true);
    expect(feedConnected(result.current)).toBe(false);
  });
});
