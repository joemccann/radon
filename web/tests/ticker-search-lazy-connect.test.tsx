/**
 * @vitest-environment jsdom
 *
 * TickerSearch lazy connect.
 *
 * The Header (and with it TickerSearch) lives in the per-page WorkspaceShell,
 * so it remounts on every App Router navigation — and stays mounted-but-hidden
 * under the mobile shell. Its former mount-time connect therefore fetched a
 * fresh ws-ticket and opened a fresh relay socket on EVERY page change, part
 * of the 2026-09-01 "every page change lags" bug. The socket now opens on
 * first focus; a search dispatched while that connect is in flight queues and
 * fires on open, with no false "search unavailable" alarm.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn() }),
}));
vi.mock("@/lib/RealtimeAuthContext", () => ({
  useRealtimeAuth: () => async () => "clerk-token",
}));

import TickerSearch from "../components/TickerSearch";

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
}

let wsInstances: MockWebSocket[] = [];
let ticketFetches = 0;

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  wsInstances = [];
  ticketFetches = 0;
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", class extends MockWebSocket {
    constructor(url: string) { super(url); wsInstances.push(this); }
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/api/ib/ws-ticket")) {
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

describe("TickerSearch lazy connect", () => {
  it("mount opens no socket and fetches no ws-ticket", async () => {
    render(<TickerSearch onSelect={vi.fn()} />);
    await flush();
    await act(async () => { vi.advanceTimersByTime(2_000); });
    expect(wsInstances).toHaveLength(0);
    expect(ticketFetches).toBe(0);
  });

  it("remount (a route change) without focus still opens no socket", async () => {
    const view = render(<TickerSearch key="a" onSelect={vi.fn()} />);
    await flush();
    view.rerender(<TickerSearch key="b" onSelect={vi.fn()} />);
    await flush();
    expect(wsInstances).toHaveLength(0);
    expect(ticketFetches).toBe(0);
  });

  it("first focus opens exactly one ticketed socket", async () => {
    render(<TickerSearch onSelect={vi.fn()} />);
    act(() => { fireEvent.focus(screen.getByRole("combobox")); });
    await flush();
    expect(wsInstances).toHaveLength(1);
    expect(ticketFetches).toBe(1);
    // A second focus reuses the connection.
    act(() => { fireEvent.blur(screen.getByRole("combobox")); });
    act(() => { fireEvent.focus(screen.getByRole("combobox")); });
    await flush();
    expect(wsInstances).toHaveLength(1);
    expect(ticketFetches).toBe(1);
  });

  it("a search typed during the focus-connect queues silently and fires on open", async () => {
    const onSearchUnavailable = vi.fn();
    render(<TickerSearch onSelect={vi.fn()} onSearchUnavailable={onSearchUnavailable} />);
    const input = screen.getByRole("combobox");
    act(() => { fireEvent.focus(input); });
    act(() => { fireEvent.change(input, { target: { value: "AAPL" } }); });
    act(() => { vi.advanceTimersByTime(300); });
    await flush();

    expect(onSearchUnavailable).not.toHaveBeenCalled();

    const ws = wsInstances[wsInstances.length - 1];
    expect(ws).toBeDefined();
    act(() => ws.simulateOpen());
    const searches = ws.sent.map((s) => JSON.parse(s)).filter((m) => m.action === "search");
    expect(searches).toEqual([{ action: "search", pattern: "AAPL" }]);
  });
});
