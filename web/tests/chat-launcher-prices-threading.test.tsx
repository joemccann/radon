/**
 * @vitest-environment jsdom
 *
 * Assistant order surface — the quote map actually reaches the gate.
 *
 * chat-approval-gate-quote-telemetry.test.tsx proves ChatPanel renders the
 * shared nine-field block once it HAS a `prices` map. That is only half the
 * story: ChatPanel does not own quotes and never opens its own relay socket,
 * so if nothing hands it the map the gate silently renders the empty
 * "No real-time data" panel in the running app and the operator confirms a
 * live order with no bid/ask in front of them.
 *
 * The map already exists exactly once, in WorkspaceShell (`usePrices` plus the
 * previous-close backfill). Pinned here is the path from that single owner to
 * the gate: WorkspaceShell -> ChatLauncher -> ChatPanel.
 *
 * TEST_AUDIT T-180: the second case used to read WorkspaceShell.tsx AS TEXT and
 * assert the substrings `prices={prices}` and `const prices = usePreviousClose(`.
 * Both survive `const prices = usePreviousClose({})` verbatim — the map can be
 * emptied at its source, the gate renders the empty "No real-time data" panel,
 * and the operator confirms a LIVE ORDER with no bid/ask. It is replaced by a
 * wire test: the real WorkspaceShell, `usePrices` and `usePreviousClose`
 * UNMOCKED, a stubbed relay socket, and an assertion on the BID/ASK the gate
 * actually paints.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioData } from "@/lib/types";

// The relay socket + the gate render are both async; 8s ceilings below need a
// per-test timeout above vitest's 5000 default (the T-177 / T-161 pattern).
vi.setConfig({ testTimeout: 15_000, hookTimeout: 15_000 });

// `passthrough` swaps the ChatPanel spy for the REAL component. The first case
// wants the spy (it asserts the prop ChatLauncher hands down); the wire case
// wants the real gate, because a spy prop is not the wire.
const chatPanelProps = vi.hoisted(() => ({
  current: null as Record<string, unknown> | null,
  passthrough: false,
}));

vi.mock("@/components/ChatPanel", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/ChatPanel")>();
  const Real = actual.default;
  return {
    default: (props: Record<string, unknown>) => {
      chatPanelProps.current = props;
      if (chatPanelProps.passthrough) return <Real {...(props as never)} />;
      return <div data-testid="chat-panel" />;
    },
  };
});

import ChatLauncher from "@/components/ChatLauncher";

afterEach(() => {
  cleanup();
  chatPanelProps.current = null;
});

const MU: PriceData = {
  symbol: "MU",
  last: 121.5,
  lastIsCalculated: false,
  bid: 121.4,
  ask: 121.6,
  bidSize: 4,
  askSize: 7,
  volume: 8_100_000,
  high: 123.1,
  low: 119.8,
  open: 120.2,
  close: 120.0,
  week52High: null,
  week52Low: null,
  avgVolume: null,
  delta: null,
  gamma: null,
  theta: null,
  vega: null,
  impliedVol: null,
  undPrice: null,
  timestamp: new Date().toISOString(),
} as PriceData;

describe("assistant gate quote plumbing", () => {
  it("ChatLauncher forwards the live quote map to ChatPanel", () => {
    render(
      <ChatLauncher
        activeSection="dashboard"
        portfolio={{ positions: [] } as never}
        prices={{ MU }}
      />,
    );

    fireEvent.keyDown(document, { key: "j", ctrlKey: true });

    expect(chatPanelProps.current).not.toBeNull();
    expect(chatPanelProps.current?.prices).toEqual({ MU });
  });
});

/* ────────────────────────────────────────────────────────────────────────────
 * The wire: WorkspaceShell -> ChatLauncher -> ChatPanel -> ApprovalGate.
 *
 * `usePrices` and `usePreviousClose` are deliberately NOT mocked — they are the
 * two links the old textual assertion pretended to cover. Everything mocked
 * below is an INPUT to the shell (portfolio/orders/watchlist/route) or a
 * sibling panel that never touches the quote map.
 * ──────────────────────────────────────────────────────────────────────────── */

const tickerDetail = vi.hoisted(() => ({
  chainContracts: [] as unknown[],
  depthSymbol: null,
  depthSymbols: [] as string[],
  depthFutureExpiry: null,
  setActiveTicker: () => {},
  setPrices: () => {},
  setFundamentals: () => {},
  setPortfolio: () => {},
  setOrders: () => {},
  setDepths: () => {},
  setTape: () => {},
}));

const portfolioStub = vi.hoisted(() => ({
  current: null as unknown,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/usePortfolio", () => ({
  usePortfolio: () => ({
    data: portfolioStub.current,
    loading: false,
    syncing: false,
    error: null,
    lastSync: "2026-08-28T13:31:00Z",
    syncNow: () => {},
  }),
}));

vi.mock("@/lib/useOrders", () => ({
  useOrders: () => ({
    data: null,
    loading: false,
    syncing: false,
    error: null,
    lastSync: null,
    syncNow: () => {},
    updateData: () => {},
  }),
}));

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ watchlist: [] }),
}));

vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({ drainNotifications: () => [], setOrdersUpdater: () => {} }),
}));

vi.mock("@/lib/TickerDetailContext", () => ({
  useTickerDetail: () => tickerDetail,
}));

vi.mock("@/components/Sidebar", () => ({ default: () => null }));
vi.mock("@/components/Header", () => ({
  default: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/dashboard/DashboardSurface", () => ({ default: () => null }));
vi.mock("@/components/FooterTelemetryStrip", () => ({ default: () => null }));
vi.mock("@/components/CommandPalette", () => ({ default: () => null }));
vi.mock("@/components/DemoWelcomeModal", () => ({ default: () => null }));
vi.mock("@/components/OfflineBanner", () => ({ default: () => null }));
vi.mock("@/components/mobile/MobileShell", () => ({ default: () => null }));
vi.mock("@/components/Toast", () => ({ default: () => null }));

import WorkspaceShell from "@/components/WorkspaceShell";

/** Relay payload: a live MU book with NO previous close, so the shell's
 *  `usePreviousClose` backfill is the only thing that can fill the DAY row. */
const RELAY_MU: PriceData = { ...MU, close: null };
const PREVIOUS_CLOSE = 120.0;

const PORTFOLIO = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-08-28T13:31:00Z",
  positions: [
    {
      ticker: "MU",
      structure_type: "Stock",
      legs: [],
      expiry: null,
    },
  ],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
} as unknown as PortfolioData;

const STOCK_PROPOSAL = {
  tool: "place_order",
  destructive: true as const,
  input: {
    type: "stock" as const,
    ticker: "MU",
    action: "BUY" as const,
    quantity: 100,
    limit_price: 121.5,
  },
  summary: "BUY 100 MU @ 121.50",
  toolUseId: "tu-wire-1",
};

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
}

let sockets: MockWebSocket[] = [];

function priceBarLabels(): string[] {
  return Array.from(document.querySelectorAll(".price-bar-label")).map((el) => el.textContent ?? "");
}

function priceBarValueFor(label: string): string | null {
  for (const row of Array.from(document.querySelectorAll(".price-bar-item"))) {
    if (row.querySelector(".price-bar-label")?.textContent === label) {
      return row.querySelector(".price-bar-value")?.textContent ?? null;
    }
  }
  return null;
}

/** Drain the microtasks the async connect IIFE awaits before `new WebSocket()`. */
async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("WorkspaceShell -> gate: the live quote map reaches the confirm card", () => {
  beforeEach(() => {
    sockets = [];
    chatPanelProps.passthrough = true;
    portfolioStub.current = PORTFOLIO;
    vi.stubGlobal("WebSocket", class extends MockWebSocket {
      constructor(url: string) { super(url); sockets.push(this); }
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/previous-close")) {
        return { ok: true, status: 200, json: async () => ({ closes: { MU: PREVIOUS_CLOSE } }) } as Response;
      }
      if (url.includes("/api/assistant")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ content: "Proposing an order.", proposal: STOCK_PROPOSAL }),
        } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    }));
  });

  afterEach(() => {
    chatPanelProps.passthrough = false;
    vi.unstubAllGlobals();
  });

  it("paints the relay's own BID/ASK on the confirm card, not an empty panel", async () => {
    render(<WorkspaceShell section="dashboard" />);

    // The shell's ONE relay subscription opens and delivers the MU book.
    await flush();
    expect(sockets.length).toBeGreaterThan(0);
    await act(async () => {
      sockets[sockets.length - 1].simulateOpen();
      sockets[sockets.length - 1].simulateMessage({ type: "price", data: RELAY_MU });
    });

    // ⌘J opens the launcher, which mounts the real ChatPanel.
    await act(async () => {
      fireEvent.keyDown(document, { key: "j", ctrlKey: true });
    });

    const textarea = await screen.findByLabelText("Ask Radon");
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "buy me 100 mu" } });
      fireEvent.submit(textarea.closest("form")!);
    });

    await screen.findByRole("button", { name: /confirm/i }, { timeout: 8000 });

    // The gate is quoting MU, and the numbers are the RELAY's, not a stub's.
    expect(priceBarLabels()).toContain("MU");
    expect(priceBarValueFor("BID")).toBe("$121.40");
    expect(priceBarValueFor("ASK")).toBe("$121.60");
    expect(screen.queryByText("No real-time data")).toBeNull();
  });

  it("carries the previous-close backfill through to the gate's DAY row", async () => {
    render(<WorkspaceShell section="dashboard" />);

    await flush();
    await act(async () => {
      sockets[sockets.length - 1].simulateOpen();
      sockets[sockets.length - 1].simulateMessage({ type: "price", data: RELAY_MU });
    });
    // Let the /api/previous-close POST resolve and re-render.
    await flush();
    await flush();

    await act(async () => {
      fireEvent.keyDown(document, { key: "j", ctrlKey: true });
    });

    const textarea = await screen.findByLabelText("Ask Radon");
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "buy me 100 mu" } });
      fireEvent.submit(textarea.closest("form")!);
    });

    await screen.findByRole("button", { name: /confirm/i }, { timeout: 8000 });

    // last 121.50 vs the BACKFILLED close 120.00. The raw socket map has
    // close=null, so this row is "---" unless usePreviousClose is in the path.
    expect(priceBarValueFor("DAY")).toBe("+1.25%");
  });
});
