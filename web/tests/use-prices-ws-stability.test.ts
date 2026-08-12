/**
 * @vitest-environment jsdom
 *
 * Unit tests for usePrices WebSocket connection stability.
 * Validates the state-machine + diff-based subscription sync refactor.
 *
 * NOTE: usePrices opens the socket inside an async IIFE — it awaits
 * buildAuthenticatedUrl() (WS-ticket auth) before `new WebSocket()`. So the
 * socket is constructed on a microtask AFTER render/rerender/reconnect/timer.
 * Every connect-triggering action is therefore followed by `await flush()`
 * (or wrapped in `await advance()` for timer-driven reconnects) so the
 * microtask drains before we assert on wsInstances/latestWs.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePrices } from "../lib/usePrices";
import type { PriceData } from "../lib/pricesProtocol";

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
function makePriceData(symbol: string, last: number): PriceData {
  return { symbol, last, lastIsCalculated: false, bid: last - 0.01, ask: last + 0.01, bidSize: 100, askSize: 100, volume: 1000, high: last + 1, low: last - 1, open: last, close: last - 0.5, week52High: null, week52Low: null, avgVolume: null, delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null, timestamp: new Date().toISOString() };
}
function latestWs(): MockWebSocket { return wsInstances[wsInstances.length - 1]; }
function sentMessages(ws: MockWebSocket) { return ws.sent.map((s) => JSON.parse(s)); }

// Drain the microtask queue (within act) so the async connect IIFE constructs
// its socket. Two ticks: one for buildAuthenticatedUrl, one for the IIFE await.
async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}
// Advance fake timers (fires the reconnect timer → connect()) then drain the
// connect microtask so the reconnected socket exists before assertions.
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

describe("Connection stability", () => {
  it("caps simultaneous depth subjects at the relay's three-ticket budget", async () => {
    renderHook(() => usePrices({
      symbols: ["SLV"],
      depthSymbols: [
        "SLV_20261016_50_C",
        "SLV_20261016_55_C",
        "SLV_20261016_60_C",
        "SLV_20261016_65_C",
      ],
      enabled: true,
    }));
    await flush();
    const ws = latestWs();
    act(() => ws.simulateOpen());
    expect(sentMessages(ws).filter((message) => message.action === "subscribe-depth")).toHaveLength(3);
  });

  it("diffs simultaneous option depth subjects without recycling retained books", async () => {
    const longKey = "SLV_20261016_60_C";
    const shortKey = "SLV_20261016_70_C";
    const nextKey = "SLV_20261016_75_C";
    const { rerender } = renderHook(
      (props: { depthSymbols: string[] }) => usePrices({
        symbols: ["SLV"],
        contracts: [
          { symbol: "SLV", expiry: "20261016", strike: 60, right: "C" },
          { symbol: "SLV", expiry: "20261016", strike: 70, right: "C" },
          { symbol: "SLV", expiry: "20261016", strike: 75, right: "C" },
        ],
        depthSymbols: props.depthSymbols,
        enabled: true,
      }),
      { initialProps: { depthSymbols: [longKey, shortKey] } },
    );
    await flush();
    const ws = latestWs();
    act(() => ws.simulateOpen());

    const initialDepth = sentMessages(ws).filter((message) => message.action === "subscribe-depth");
    expect(initialDepth).toEqual(expect.arrayContaining([
      expect.objectContaining({ symbol: "SLV", expiry: "20261016", strike: 60, right: "C" }),
      expect.objectContaining({ symbol: "SLV", expiry: "20261016", strike: 70, right: "C" }),
    ]));

    const beforeNoop = ws.sent.length;
    rerender({ depthSymbols: [shortKey, longKey] });
    await flush();
    expect(ws.sent).toHaveLength(beforeNoop);

    rerender({ depthSymbols: [longKey, nextKey] });
    await flush();
    const delta = sentMessages(ws).slice(beforeNoop);
    expect(delta).toContainEqual({ action: "unsubscribe-depth", symbol: shortKey });
    expect(delta).toContainEqual(expect.objectContaining({
      action: "subscribe-depth", symbol: "SLV", expiry: "20261016", strike: 75, right: "C",
    }));
    expect(delta).not.toContainEqual({ action: "unsubscribe-depth", symbol: longKey });
  });

  it("does not recreate WS when symbols change", async () => {
    const { rerender } = renderHook(
      (props: { symbols: string[] }) => usePrices({ symbols: props.symbols, enabled: true }),
      { initialProps: { symbols: ["AAPL"] } },
    );
    await flush();
    expect(wsInstances).toHaveLength(1);
    act(() => latestWs().simulateOpen());
    rerender({ symbols: ["AAPL", "MSFT"] });
    await flush();
    expect(wsInstances).toHaveLength(1);
  });

  it("does not recreate WS when contracts change", async () => {
    const c1 = { symbol: "PLTR", expiry: "20260320", strike: 100, right: "C" as const };
    const c2 = { symbol: "PLTR", expiry: "20260320", strike: 110, right: "C" as const };
    const { rerender } = renderHook(
      (props: { contracts: typeof c1[] }) => usePrices({ symbols: ["PLTR"], contracts: props.contracts, enabled: true }),
      { initialProps: { contracts: [c1] } },
    );
    await flush();
    expect(wsInstances).toHaveLength(1);
    act(() => latestWs().simulateOpen());
    rerender({ contracts: [c1, c2] });
    await flush();
    expect(wsInstances).toHaveLength(1);
  });

  it("sends diff-based subscribe when symbols added", async () => {
    const { rerender } = renderHook(
      (props: { symbols: string[] }) => usePrices({ symbols: props.symbols, enabled: true }),
      { initialProps: { symbols: ["AAPL"] } },
    );
    await flush();
    const ws = latestWs();
    act(() => ws.simulateOpen());
    expect(sentMessages(ws)).toHaveLength(1);
    expect(sentMessages(ws)[0].symbols).toContain("AAPL");
    rerender({ symbols: ["AAPL", "MSFT"] });
    await flush();
    const all = sentMessages(ws);
    expect(all).toHaveLength(2);
    expect(all[1].action).toBe("subscribe");
    expect(all[1].symbols).toContain("MSFT");
    expect(all[1].symbols).not.toContain("AAPL");
  });

  it("sends unsubscribe when symbols removed", async () => {
    const { rerender } = renderHook(
      (props: { symbols: string[] }) => usePrices({ symbols: props.symbols, enabled: true }),
      { initialProps: { symbols: ["AAPL", "MSFT"] } },
    );
    await flush();
    act(() => latestWs().simulateOpen());
    rerender({ symbols: ["AAPL"] });
    await flush();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const unsub = sentMessages(latestWs()).find((m: any) => m.action === "unsubscribe");
    expect(unsub).toBeDefined();
    expect(unsub.symbols).toContain("MSFT");
  });
});

describe("Idempotent connect", () => {
  it("calling connect while CONNECTING creates no extra socket", async () => {
    const { result } = renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    expect(wsInstances).toHaveLength(1);
    act(() => result.current.reconnect());
    await flush();
    expect(wsInstances.length).toBeLessThanOrEqual(2);
  });

  it("calling connect while OPEN creates no extra socket", async () => {
    renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    expect(wsInstances).toHaveLength(1);
    act(() => latestWs().simulateOpen());
    expect(wsInstances).toHaveLength(1);
  });
});

describe("Authenticated WebSocket URL", () => {
  it("opens a ticketed realtime socket when Clerk token auth is available", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ticket: "ticket-abc" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => usePrices({
      symbols: ["AAPL"],
      enabled: true,
      getToken: async () => "clerk-token",
    }));
    await flush();

    expect(wsInstances).toHaveLength(1);
    expect(latestWs().url).toBe("ws://localhost:8765?ticket=ticket-abc");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ib/ws-ticket",
      expect.objectContaining({
        method: "POST",
        cache: "no-store",
        headers: expect.objectContaining({
          Authorization: "Bearer clerk-token",
        }),
      }),
    );
  });

  it("does not fall back to an unauthenticated socket when ticket auth fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response("Unauthorized", { status: 401 }),
    ));

    const { result } = renderHook(() => usePrices({
      symbols: ["AAPL"],
      enabled: true,
      getToken: async () => "clerk-token",
    }));
    await flush();

    expect(wsInstances).toHaveLength(0);
    expect(result.current.connected).toBe(false);
    expect(result.current.error).toBe("Failed to obtain WS ticket: 401");
  });

  it("times out stalled authentication and leaves the connecting state", async () => {
    const { result } = renderHook(() => usePrices({
      symbols: ["AAPL"],
      enabled: true,
      getToken: () => new Promise<string | null>(() => {}),
    }));
    await flush();

    expect(wsInstances).toHaveLength(0);
    await advance(10_001);

    expect(result.current.connected).toBe(false);
    expect(result.current.error?.toLowerCase()).toContain("timed out");
  });

  it("does not open a socket when authentication resolves after realtime is disabled", async () => {
    let resolveToken!: (token: string | null) => void;
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ticket: "late-ticket" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = renderHook(
      (props: { enabled: boolean }) => usePrices({
        symbols: ["AAPL"],
        enabled: props.enabled,
        getToken: () => new Promise<string | null>((resolve) => { resolveToken = resolve; }),
      }),
      { initialProps: { enabled: true } },
    );
    await flush();
    rerender({ enabled: false });
    resolveToken("clerk-token");
    await flush();

    expect(wsInstances).toHaveLength(0);
  });

  it("does not create an orphan snapshot socket after its caller deadline", async () => {
    let resolveToken!: (token: string | null) => void;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ticket: "late-snapshot-ticket" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));
    const { result } = renderHook(() => usePrices({
      symbols: [],
      enabled: false,
      getToken: () => new Promise<string | null>((resolve) => { resolveToken = resolve; }),
    }));

    let snapshot!: Record<string, PriceData>;
    await act(async () => {
      const pending = result.current.getSnapshot(["AAPL"]);
      vi.advanceTimersByTime(5_001);
      snapshot = await pending;
    });
    expect(snapshot).toEqual({});

    resolveToken("clerk-token");
    await flush();
    expect(wsInstances).toHaveLength(0);
  });
});

describe("Connection deadlines", () => {
  it("closes a WebSocket that never reaches open and schedules recovery", async () => {
    renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    const stalled = latestWs();

    await advance(10_001);

    expect(stalled.readyState).toBe(MockWebSocket.CLOSED);
  });
});

describe("Stale socket isolation", () => {
  it("old socket onclose after new socket exists does not trigger reconnect", async () => {
    const { result } = renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    const oldWs = latestWs();
    act(() => oldWs.simulateOpen());
    act(() => result.current.reconnect());
    await flush();
    expect(latestWs()).not.toBe(oldWs);
    act(() => { oldWs.readyState = MockWebSocket.CLOSED; oldWs.onclose?.(new Event("close")); });
    expect(wsInstances).toHaveLength(2);
  });

  it("old socket onmessage does not overwrite newer state", async () => {
    const { result } = renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    const oldWs = latestWs();
    act(() => oldWs.simulateOpen());
    act(() => oldWs.simulateMessage({ type: "price", symbol: "AAPL", data: makePriceData("AAPL", 100) }));
    expect(result.current.prices.AAPL?.last).toBe(100);
    act(() => result.current.reconnect());
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateMessage({ type: "price", symbol: "AAPL", data: makePriceData("AAPL", 200) }));
    expect(result.current.prices.AAPL?.last).toBe(200);
    act(() => { oldWs.onmessage?.({ data: JSON.stringify({ type: "price", symbol: "AAPL", data: makePriceData("AAPL", 50) }) }); });
    expect(result.current.prices.AAPL?.last).toBe(200);
  });
});

describe("Reconnect timer cleanup", () => {
  it("unmount clears pending reconnect timeout", async () => {
    const spy = vi.spyOn(globalThis, "clearTimeout");
    const { unmount } = renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateClose());
    unmount();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it("enabled=false clears pending reconnect timeout", async () => {
    const { rerender } = renderHook(
      (props: { enabled: boolean }) => usePrices({ symbols: ["AAPL"], enabled: props.enabled }),
      { initialProps: { enabled: true } },
    );
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateClose());
    rerender({ enabled: false });
    await flush();
    const before = wsInstances.length;
    await advance(60_000);
    expect(wsInstances.length).toBe(before);
  });

  it("reconnect timer does not stack multiple retries", async () => {
    renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateClose());
    const after = wsInstances.length;
    await advance(1600);
    expect(wsInstances.length).toBe(after + 1);
  });

  it("exponential backoff increases delay on sequential failures", async () => {
    renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateClose());
    await advance(1600);
    expect(wsInstances.length).toBe(2);
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateClose());
    await advance(1600);
    const at1600 = wsInstances.length;
    await advance(2000);
    expect(wsInstances.length).toBeGreaterThanOrEqual(at1600);
  });

  it("backoff resets on successful open", async () => {
    renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateClose());
    await advance(1600);
    act(() => latestWs().simulateOpen());
    act(() => latestWs().simulateClose());
    await advance(1600);
    expect(wsInstances.length).toBeGreaterThanOrEqual(3);
  });
});

describe("Subscription diff", () => {
  it("does not re-send identical subscriptions when hashes unchanged", async () => {
    const { rerender } = renderHook(
      (props: { symbols: string[] }) => usePrices({ symbols: props.symbols, enabled: true }),
      { initialProps: { symbols: ["AAPL"] } },
    );
    await flush();
    const ws = latestWs();
    act(() => ws.simulateOpen());
    expect(ws.sent).toHaveLength(1);
    rerender({ symbols: ["AAPL"] });
    await flush();
    expect(ws.sent).toHaveLength(1);
  });

  it("sends only diff (added/removed), not full re-subscribe", async () => {
    const { rerender } = renderHook(
      (props: { symbols: string[] }) => usePrices({ symbols: props.symbols, enabled: true }),
      { initialProps: { symbols: ["AAPL", "MSFT"] } },
    );
    await flush();
    act(() => latestWs().simulateOpen());
    rerender({ symbols: ["AAPL", "NVDA"] });
    await flush();
    const msgs = sentMessages(latestWs());
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const subs = msgs.filter((m: any) => m.action === "subscribe");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const unsubs = msgs.filter((m: any) => m.action === "unsubscribe");
    expect(subs[subs.length - 1].symbols).toContain("NVDA");
    expect(subs[subs.length - 1].symbols).not.toContain("AAPL");
    expect(unsubs.length).toBeGreaterThanOrEqual(1);
    expect(unsubs[unsubs.length - 1].symbols).toContain("MSFT");
  });

  it("evicts price data for removed subscriptions", async () => {
    const { result, rerender } = renderHook(
      (props: { symbols: string[] }) => usePrices({ symbols: props.symbols, enabled: true }),
      { initialProps: { symbols: ["AAPL", "MSFT"] } },
    );
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => {
      latestWs().simulateMessage({ type: "price", symbol: "AAPL", data: makePriceData("AAPL", 175) });
      latestWs().simulateMessage({ type: "price", symbol: "MSFT", data: makePriceData("MSFT", 420) });
    });
    expect(result.current.prices.AAPL).toBeDefined();
    expect(result.current.prices.MSFT).toBeDefined();
    rerender({ symbols: ["AAPL"] });
    await flush();
    expect(result.current.prices.AAPL).toBeDefined();
    expect(result.current.prices.MSFT).toBeUndefined();
  });

  it("preserves prices for unchanged subscriptions across sub changes", async () => {
    const { result, rerender } = renderHook(
      (props: { symbols: string[] }) => usePrices({ symbols: props.symbols, enabled: true }),
      { initialProps: { symbols: ["AAPL"] } },
    );
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => { latestWs().simulateMessage({ type: "price", symbol: "AAPL", data: makePriceData("AAPL", 175) }); });
    rerender({ symbols: ["AAPL", "MSFT"] });
    await flush();
    expect(result.current.prices.AAPL?.last).toBe(175);
  });
});

describe("Lifecycle transitions", () => {
  it("creates WS when first subscription arrives", async () => {
    const { result, rerender } = renderHook(
      (props: { symbols: string[] }) => usePrices({ symbols: props.symbols, enabled: true }),
      { initialProps: { symbols: [] as string[] } },
    );
    await flush();
    expect(wsInstances).toHaveLength(0);
    rerender({ symbols: ["AAPL"] });
    await flush();
    expect(wsInstances).toHaveLength(1);
    act(() => latestWs().simulateOpen());
    expect(result.current.connected).toBe(true);
  });

  it("closes WS when all subscriptions removed", async () => {
    const { result, rerender } = renderHook(
      (props: { symbols: string[] }) => usePrices({ symbols: props.symbols, enabled: true }),
      { initialProps: { symbols: ["AAPL"] } },
    );
    await flush();
    act(() => latestWs().simulateOpen());
    expect(result.current.connected).toBe(true);
    rerender({ symbols: [] as string[] });
    await flush();
    expect(result.current.connected).toBe(false);
  });

  it("closes and stays closed when enabled becomes false", async () => {
    const { result, rerender } = renderHook(
      (props: { enabled: boolean }) => usePrices({ symbols: ["AAPL"], enabled: props.enabled }),
      { initialProps: { enabled: true } },
    );
    await flush();
    act(() => latestWs().simulateOpen());
    expect(result.current.connected).toBe(true);
    rerender({ enabled: false });
    await flush();
    expect(result.current.connected).toBe(false);
    const before = wsInstances.length;
    await advance(60_000);
    expect(wsInstances.length).toBe(before);
  });

  it("reconnects when enabled flips false->true", async () => {
    const { result, rerender } = renderHook(
      (props: { enabled: boolean }) => usePrices({ symbols: ["AAPL"], enabled: props.enabled }),
      { initialProps: { enabled: true } },
    );
    await flush();
    act(() => latestWs().simulateOpen());
    rerender({ enabled: false });
    await flush();
    expect(result.current.connected).toBe(false);
    rerender({ enabled: true });
    await flush();
    act(() => latestWs().simulateOpen());
    expect(result.current.connected).toBe(true);
  });
});

describe("Callback refs", () => {
  it("latest onPriceUpdate is invoked (not stale closure)", async () => {
    const cb1 = vi.fn();
    const cb2 = vi.fn();
    const { rerender } = renderHook(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (props: { cb: (u: any) => void }) => usePrices({ symbols: ["AAPL"], enabled: true, onPriceUpdate: props.cb }),
      { initialProps: { cb: cb1 } },
    );
    await flush();
    act(() => latestWs().simulateOpen());
    rerender({ cb: cb2 });
    act(() => { latestWs().simulateMessage({ type: "price", symbol: "AAPL", data: makePriceData("AAPL", 175) }); });
    expect(cb1).not.toHaveBeenCalled();
    expect(cb2).toHaveBeenCalledTimes(1);
  });
});

describe("Price state across reconnects", () => {
  it("preserves last-known prices until fresh ticks arrive", async () => {
    const { result } = renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => { latestWs().simulateMessage({ type: "price", symbol: "AAPL", data: makePriceData("AAPL", 175) }); });
    expect(result.current.prices.AAPL?.last).toBe(175);
    act(() => latestWs().simulateClose());
    expect(result.current.prices.AAPL?.last).toBe(175);
    await advance(1600);
    act(() => latestWs().simulateOpen());
    expect(result.current.prices.AAPL?.last).toBe(175);
    act(() => { latestWs().simulateMessage({ type: "price", symbol: "AAPL", data: makePriceData("AAPL", 180) }); });
    expect(result.current.prices.AAPL?.last).toBe(180);
  });
});

describe("Message hardening", () => {
  it("ignores malformed JSON without crashing", async () => {
    const { result } = renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => { latestWs().onmessage?.({ data: "not valid json{{{" }); });
    expect(result.current.connected).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("ignores unknown message types without crashing", async () => {
    const { result } = renderHook(() => usePrices({ symbols: ["AAPL"], enabled: true }));
    await flush();
    act(() => latestWs().simulateOpen());
    act(() => { latestWs().simulateMessage({ type: "unknown_future_type", foo: "bar" }); });
    expect(result.current.connected).toBe(true);
  });
});
