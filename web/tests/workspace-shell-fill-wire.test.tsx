/**
 * @vitest-environment jsdom
 *
 * T-464: fill-driven portfolio refresh asserted through WorkspaceShell ITSELF.
 *
 * fill-driven-portfolio-refresh-wire.test.tsx renders a hand-cloned harness
 * that has already drifted from production (it passes useFillToasts three
 * arguments; WorkspaceShell.tsx:430 passes four, including hasToastKey).
 * Remove or misorder the onNewFills argument at that call site and the
 * harness still passes while the positions table silently stops refreshing
 * on fills (R-640 / T-459).
 *
 * This file renders the real WorkspaceShell with only its DATA hooks mocked:
 * useToast, useFillToasts, usePortfolio (the owner of every /api/portfolio
 * request), and the line-430 wiring are all production code. fetch is
 * stubbed at the global; a new execId in the orders payload must produce
 * exactly one POST /api/portfolio on the wire.
 */
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExecutedOrder, OrderContract, OrdersData, PortfolioData } from "@/lib/types";
import { SEEN_STORAGE_KEY } from "@/lib/fillToasts";

// ---- data-hook / chrome mocks (WorkspaceShell's fill wiring stays real) ----

let ordersData: OrdersData | null = null;

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("next/dynamic", () => ({ default: () => () => null }));
vi.mock("@/lib/ThemeContext", () => ({ useTheme: () => ({ theme: "dark", toggleTheme: vi.fn() }) }));
vi.mock("@/lib/useViewport", () => ({ useViewport: () => ({ isMobile: false, hasMounted: true }) }));
vi.mock("@/lib/useOrders", () => ({
  useOrders: () => ({
    data: ordersData,
    loading: false,
    syncing: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
    updateData: vi.fn(),
  }),
}));
vi.mock("@/lib/useMarketHours", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/useMarketHours")>();
  return { ...actual, useMarketHours: () => actual.MarketState.CLOSED };
});
vi.mock("@/lib/useAutoSyncOnStale", () => ({ useAutoSyncOnStale: () => {} }));
vi.mock("@/lib/useSnapshotStaleness", () => ({
  useSnapshotStaleness: () => ({ isStale: false, state: "fresh", staleAgeMinutes: 0, tick: 0 }),
}));
vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({ drainNotifications: () => [], setOrdersUpdater: () => {} }),
}));
vi.mock("@/lib/RealtimePricesContext", () => ({
  useRealtimePrices: () => ({
    prices: {},
    fundamentals: {},
    depths: {},
    tape: {},
    connected: false,
    ibConnected: true,
    ibIssue: null,
    ibStatusMessage: null,
    error: null,
    publishSubscriptions: () => {},
  }),
}));
vi.mock("@/lib/useIndexQuoteFallback", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/useIndexQuoteFallback")>();
  return { ...actual, useIndexQuoteFallback: () => ({}) };
});
vi.mock("@/lib/useFuturesQuoteFallback", () => ({ useFuturesQuoteFallback: () => ({}) }));
vi.mock("@/lib/usePreviousClose", () => ({
  usePreviousClose: (p: Record<string, unknown>) => p,
}));
vi.mock("@/lib/futuresSession", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/futuresSession")>();
  return { ...actual, useGlobexOpen: () => false };
});
vi.mock("@/lib/useWatchlist", () => ({ useWatchlist: () => ({ watchlist: [] }) }));
vi.mock("@/lib/TickerDetailContext", () => ({
  useTickerDetail: () => ({
    chainContracts: [],
    depthSymbol: null,
    depthSymbols: [],
    depthFutureExpiry: null,
    setActiveTicker: () => {},
    setPrices: () => {},
    setFundamentals: () => {},
    setPortfolio: () => {},
    setOrders: () => {},
    setDepths: () => {},
    setTape: () => {},
  }),
}));
vi.mock("@/lib/offline/OfflineStatusContext", () => ({ useOfflineStatus: () => ({ offline: false }) }));

// Presentational chrome only — none of these own the fill wiring or a fetch.
vi.mock("@/components/Sidebar", () => ({ default: () => null }));
vi.mock("@/components/Header", () => ({ default: () => null }));
vi.mock("@/components/MetricCards", () => ({ default: () => null }));
vi.mock("@/components/dashboard/DashboardSurface", () => ({ default: () => null }));
vi.mock("@/components/ChatLauncher", () => ({ default: () => null }));
vi.mock("@/components/DemoWelcomeModal", () => ({ default: () => null }));
vi.mock("@/components/mobile/MobileShell", () => ({ default: () => null }));
vi.mock("@/components/FooterTelemetryStrip", () => ({ default: () => null }));
vi.mock("@/components/CommandPalette", () => ({ default: () => null }));
vi.mock("@/components/OfflineBanner", () => ({ default: () => null }));
vi.mock("@/components/FuturesStrip", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/FuturesStrip")>();
  return { ...actual, default: () => null };
});
vi.mock("@/components/ui/InstrumentSkeleton", () => ({ default: () => null }));

import WorkspaceShell from "@/components/WorkspaceShell";

function makeContract(): OrderContract {
  return { conId: 12345, symbol: "VIX", secType: "OPT", strike: 30, right: "C", expiry: "20261020" };
}

function makeFill(execId: string, permId = 900_001, quantity = 2): ExecutedOrder {
  return {
    execId,
    permId,
    symbol: "VIX",
    contract: makeContract(),
    side: "BOT",
    quantity,
    avgPrice: 0.61,
    commission: null,
    realizedPNL: null,
    time: new Date().toISOString(),
    exchange: "SMART",
  } as ExecutedOrder;
}

function makeOrders(executed: ExecutedOrder[]): OrdersData {
  return {
    last_sync: new Date().toISOString(),
    open_orders: [],
    executed_orders: executed,
    open_count: 0,
    executed_count: executed.length,
  };
}

const BASELINE = [makeFill("a.1.01", 900_777), makeFill("a.2.01", 900_778)];

const PORTFOLIO_JSON = {
  positions: [],
  bankroll: 1_000_000,
  last_sync: "2026-09-05T14:00:00Z",
} as unknown as PortfolioData;

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  window.sessionStorage.removeItem(SEEN_STORAGE_KEY);
  delete process.env.NEXT_PUBLIC_RADON_DEMO;
  ordersData = null;
  fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers(),
    json: async () => PORTFOLIO_JSON,
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const portfolioPosts = () =>
  fetchMock.mock.calls.filter(
    ([url, init]) =>
      url === "/api/portfolio" && (init as RequestInit | undefined)?.method === "POST",
  );

async function renderShell() {
  const view = render(<WorkspaceShell section="dashboard" />);
  await act(async () => {});
  return {
    ...view,
    pushOrders: async (payload: OrdersData) => {
      ordersData = payload;
      view.rerender(<WorkspaceShell section="dashboard" />);
      await act(async () => {});
    },
  };
}

describe("WorkspaceShell fill-driven portfolio refresh (wire)", () => {
  it("does not hit the portfolio producer for the baseline payload", async () => {
    const shell = await renderShell();
    await shell.pushOrders(makeOrders(BASELINE));
    expect(portfolioPosts()).toHaveLength(0);
  });

  it("POSTs /api/portfolio exactly once when a new execId arrives", async () => {
    const shell = await renderShell();
    await shell.pushOrders(makeOrders(BASELINE));
    expect(portfolioPosts()).toHaveLength(0);

    await shell.pushOrders(makeOrders([...BASELINE, makeFill("b.9.01", 900_900)]));

    const posts = portfolioPosts();
    expect(posts).toHaveLength(1);
    const [url, init] = posts[0] as [string, RequestInit];
    expect(url).toBe("/api/portfolio");
    expect(init.method).toBe("POST");
  });

  it("keeps the producer off the wire for a fill in demo mode", async () => {
    process.env.NEXT_PUBLIC_RADON_DEMO = "1";
    const shell = await renderShell();
    await shell.pushOrders(makeOrders(BASELINE));
    await shell.pushOrders(makeOrders([...BASELINE, makeFill("c.7.01", 900_901)]));
    expect(portfolioPosts()).toHaveLength(0);
  });
});

describe("WorkspaceShell dismissed fill toast forgets its running total (T-465 / R-642)", () => {
  it("shows the next fill's own quantity, not the cumulative total, after dismissal", async () => {
    const shell = await renderShell();
    await shell.pushOrders(makeOrders(BASELINE));

    // First fill for the group: toast reads its quantity.
    await shell.pushOrders(makeOrders([...BASELINE, makeFill("d.1.01", 900_950, 2)]));
    expect(screen.getByText("FILLED · BUY 2x VIX $30C @ $0.61")).toBeTruthy();

    // Operator dismisses the toast (real ToastContainer, real dismissToast).
    fireEvent.click(screen.getByLabelText("Dismiss"));

    // Next fill for the SAME order (same permId): a fresh toast must read 3x,
    // not the 5x cumulative total presented as a new fill.
    await shell.pushOrders(
      makeOrders([...BASELINE, makeFill("d.1.01", 900_950, 2), makeFill("d.2.01", 900_950, 3)]),
    );
    expect(screen.getByText("FILLED · BUY 3x VIX $30C @ $0.61")).toBeTruthy();
    expect(screen.queryByText("FILLED · BUY 5x VIX $30C @ $0.61")).toBeNull();
  });
});
