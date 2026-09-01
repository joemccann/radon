/**
 * @vitest-environment jsdom
 *
 * Verifies onSearchUnavailable fires when the WS relay reports IB disconnected
 * (or the WS itself is unreachable when a search is dispatched).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, fireEvent, cleanup } from "@testing-library/react";
import React from "react";
import TickerSearch from "../components/TickerSearch";

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn() }),
}));

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
  }
  simulateOpen() { this.readyState = MockWebSocket.OPEN; this.onopen?.(new Event("open")); }
  simulateMessage(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }); }
}

let wsInstances: MockWebSocket[] = [];
function latestWs(): MockWebSocket { return wsInstances[wsInstances.length - 1]; }

async function flushSocketOpen() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

// Lazy connect (2026-09-01): the socket opens on first focus, not on mount —
// mount happens on every App Router navigation. Focus the combobox to connect.
async function focusToConnect() {
  act(() => { fireEvent.focus(screen.getByRole("combobox")); });
  await flushSocketOpen();
}

beforeEach(() => {
  wsInstances = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", class extends MockWebSocket {
    constructor(url: string) { super(url); wsInstances.push(this); }
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("TickerSearch IB-disconnected handling", () => {
  it("calls onSearchUnavailable when relay flags disconnected:true", async () => {
    const onSelect = vi.fn();
    const onSearchUnavailable = vi.fn();
    render(React.createElement(TickerSearch, { onSelect, onSearchUnavailable }));
    await focusToConnect();
    const ws = latestWs();
    act(() => ws.simulateOpen());

    const input = screen.getByRole("combobox");
    act(() => { fireEvent.change(input, { target: { value: "CRCL" } }); });
    act(() => vi.advanceTimersByTime(300));
    act(() => ws.simulateMessage({
      type: "searchResults",
      pattern: "CRCL",
      results: [],
      disconnected: true,
    }));

    expect(onSearchUnavailable).toHaveBeenCalledTimes(1);
    expect(screen.getByText("No results")).toBeDefined();
  });

  it("does NOT call onSearchUnavailable when IB is connected and just has no matches", async () => {
    const onSelect = vi.fn();
    const onSearchUnavailable = vi.fn();
    render(React.createElement(TickerSearch, { onSelect, onSearchUnavailable }));
    await focusToConnect();
    const ws = latestWs();
    act(() => ws.simulateOpen());

    const input = screen.getByRole("combobox");
    act(() => { fireEvent.change(input, { target: { value: "ZZZZZ" } }); });
    act(() => vi.advanceTimersByTime(300));
    act(() => ws.simulateMessage({
      type: "searchResults",
      pattern: "ZZZZZ",
      results: [],
    }));

    expect(onSearchUnavailable).not.toHaveBeenCalled();
    expect(screen.getByText("No results")).toBeDefined();
  });

  it("stays quiet while the focus-connect is in flight, then surfaces the failure when it dies", async () => {
    const onSelect = vi.fn();
    const onSearchUnavailable = vi.fn();
    render(React.createElement(TickerSearch, { onSelect, onSearchUnavailable }));
    await focusToConnect();
    const ws = latestWs();
    // intentionally do not simulateOpen — WS stays CONNECTING

    const input = screen.getByRole("combobox");
    act(() => { fireEvent.change(input, { target: { value: "AAPL" } }); });
    act(() => vi.advanceTimersByTime(300));

    // Dispatch races the first connect: queue silently, no false alarm.
    expect(onSearchUnavailable).not.toHaveBeenCalled();

    // The connection attempt dies with the search still queued — now tell.
    act(() => { ws.onclose?.(new Event("close")); });
    expect(onSearchUnavailable).toHaveBeenCalledTimes(1);
  });

  it("calls onSearchUnavailable when the socket is gone at dispatch time", async () => {
    const onSelect = vi.fn();
    const onSearchUnavailable = vi.fn();
    render(React.createElement(TickerSearch, { onSelect, onSearchUnavailable }));
    await focusToConnect();
    const ws = latestWs();
    act(() => ws.simulateOpen());
    // Relay drops; no connect attempt is in flight at dispatch time.
    act(() => { ws.readyState = MockWebSocket.CLOSED; ws.onclose?.(new Event("close")); });

    const input = screen.getByRole("combobox");
    act(() => { fireEvent.change(input, { target: { value: "AAPL" } }); });
    act(() => vi.advanceTimersByTime(300));

    expect(onSearchUnavailable).toHaveBeenCalledTimes(1);
  });
});
