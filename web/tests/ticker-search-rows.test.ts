/**
 * @vitest-environment jsdom
 *
 * TickerSearch result rows: company names, one row per symbol, type pill only
 * when it distinguishes rows.
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
  close() { this.readyState = MockWebSocket.CLOSED; }
  simulateOpen() { this.readyState = MockWebSocket.OPEN; this.onopen?.(new Event("open")); }
  simulateMessage(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }); }
}

let wsInstances: MockWebSocket[] = [];

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

async function searchWith(pattern: string, results: unknown[]) {
  render(React.createElement(TickerSearch, { onSelect: vi.fn() }));
  act(() => { fireEvent.focus(screen.getByRole("combobox")); });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  const ws = wsInstances[wsInstances.length - 1];
  act(() => ws.simulateOpen());
  act(() => { fireEvent.change(screen.getByRole("combobox"), { target: { value: pattern } }); });
  act(() => vi.advanceTimersByTime(300));
  act(() => ws.simulateMessage({ type: "searchResults", pattern, results }));
}

describe("TickerSearch result rows", () => {
  it("names the company, folds a second listing of the same symbol into one row, and drops a constant STK pill", async () => {
    await searchWith("NV", [
      { conId: 1, symbol: "NVS", secType: "STK", primaryExchange: "TSE", currency: "CAD", description: "NOVARTIS AG" },
      { conId: 2, symbol: "NVDA", secType: "STK", primaryExchange: "NASDAQ", currency: "USD", description: "NVIDIA CORP" },
      { conId: 3, symbol: "NVS", secType: "STK", primaryExchange: "NYSE", currency: "USD", description: "NOVARTIS AG-SPONSORED ADR" },
      { conId: 4, symbol: "NVO", secType: "STK", primaryExchange: "NYSE", currency: "USD" },
    ]);
    const rows = screen.getAllByRole("option");
    expect(rows.map((r) => r.querySelector("[data-search-symbol]")?.textContent)).toEqual(["NVS", "NVDA", "NVO"]);
    expect(rows[0].textContent).toContain("NOVARTIS AG-SPONSORED ADR");
    expect(rows[0].textContent).toContain("NYSE · also TSE");
    expect(rows[1].textContent).toContain("NVIDIA CORP");
    expect(screen.queryByText("STK")).toBeNull();
  });

  it("keeps the type pill when result types are mixed", async () => {
    await searchWith("ES", [
      { conId: 1, symbol: "ES", secType: "FUT", primaryExchange: "CME", currency: "USD" },
      { conId: 2, symbol: "ESS", secType: "STK", primaryExchange: "NYSE", currency: "USD" },
    ]);
    expect(screen.getByText("FUT")).toBeDefined();
    expect(screen.getByText("STK")).toBeDefined();
  });
});
