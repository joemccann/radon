/**
 * @vitest-environment jsdom
 *
 * T-588: /orders auto-POST is gated by the 8-minute orders-sync threshold
 * at WorkspaceShell, not only at the hook helper.
 *
 * orders-staleness-threshold.test.ts passes ORDERS_SNAPSHOT_STALE_THRESHOLD_MS
 * into useSnapshotStaleness itself. WorkspaceShell tests mock that hook.
 * Reverting the shell to 60_000 therefore stayed green while a healthy
 * 5.5-minute-old book auto-posted /api/orders for the rest of the cycle.
 *
 * This file renders the real WorkspaceShell with the real staleness and
 * auto-sync hooks. useOrders.syncNow is the owner of the POST. A book one
 * orders-sync cycle old must not fire; a book past 8 minutes must fire
 * exactly one POST /api/orders.
 */
import React from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetAutoSyncCooldowns } from "@/lib/useAutoSyncOnStale";

const NOW = new Date("2026-11-12T15:26:10Z");
const ORDERS_SYNC_INTERVAL_MS = 5 * 60_000;
const ORDERS_SYNC_TIMEOUT_MS = 30_000;
const ORDERS_THRESHOLD_MS = 8 * 60_000;

let ordersLastSync: string;
let ordersSyncNow: () => void;

vi.mock("next/navigation", () => ({ usePathname: () => "/orders" }));
vi.mock("next/dynamic", () => ({ default: () => () => null }));
vi.mock("@/lib/ThemeContext", () => ({ useTheme: () => ({ theme: "dark", toggleTheme: vi.fn() }) }));
vi.mock("@/lib/useViewport", () => ({ useViewport: () => ({ isMobile: false, hasMounted: true }) }));
vi.mock("@/lib/usePortfolio", () => ({
  usePortfolio: () => ({
    data: { positions: [], bankroll: 1_000_000, last_sync: NOW.toISOString() },
    loading: false,
    syncing: false,
    error: null,
    lastSync: NOW.toISOString(),
    syncNow: vi.fn(),
  }),
}));
vi.mock("@/lib/useOrders", () => ({
  useOrders: () => ({
    data: {
      last_sync: ordersLastSync,
      open_orders: [],
      executed_orders: [],
      open_count: 0,
      executed_count: 0,
    },
    loading: false,
    syncing: false,
    error: null,
    lastSync: ordersLastSync,
    syncNow: () => ordersSyncNow(),
    updateData: vi.fn(),
  }),
}));
vi.mock("@/lib/useMarketHours", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/useMarketHours")>();
  return { ...actual, useMarketHours: () => actual.MarketState.CLOSED };
});
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
vi.mock("@/components/Sidebar", () => ({ default: () => null }));
vi.mock("@/components/Header", () => ({ default: () => null }));
vi.mock("@/components/MetricCards", () => ({ default: () => null }));
vi.mock("@/components/dashboard/DashboardSurface", () => ({ default: () => null }));
vi.mock("@/components/ChatLauncher", () => ({ default: () => null }));
vi.mock("@/components/DemoWelcomeModal", () => ({ default: () => null }));
vi.mock("@/components/mobile/MobileShell", () => ({ default: () => null }));
vi.mock("@/components/FooterTelemetryStrip", () => ({ default: () => null }));
vi.mock("@/components/OfflineBanner", () => ({ default: () => null }));
vi.mock("@/components/FuturesStrip", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/FuturesStrip")>();
  return { ...actual, default: () => null };
});
vi.mock("@/components/ui/InstrumentSkeleton", () => ({ default: () => null }));

import WorkspaceShell from "@/components/WorkspaceShell";

let fetchMock: ReturnType<typeof vi.fn>;

function ordersPosts() {
  return fetchMock.mock.calls.filter(
    ([url, init]) =>
      url === "/api/orders" && (init as RequestInit | undefined)?.method === "POST",
  );
}

async function renderOrdersShell(ageMs: number) {
  ordersLastSync = new Date(NOW.getTime() - ageMs).toISOString();
  ordersSyncNow = () => {
    void fetch("/api/orders", { method: "POST" });
  };
  render(<WorkspaceShell section="orders" />);
  await act(async () => {});
}

describe("WorkspaceShell orders stale threshold (wire)", () => {
  beforeEach(() => {
    delete process.env.NEXT_PUBLIC_RADON_DEMO;
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    resetAutoSyncCooldowns();
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => ({}),
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    resetAutoSyncCooldowns();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("does not POST /api/orders for a book one orders-sync cycle old", async () => {
    await renderOrdersShell(ORDERS_SYNC_INTERVAL_MS + ORDERS_SYNC_TIMEOUT_MS);
    expect(ordersPosts()).toHaveLength(0);
  });

  it("POSTs /api/orders exactly once when the book is past the 8-minute threshold", async () => {
    await renderOrdersShell(ORDERS_THRESHOLD_MS + 10_000);
    const posts = ordersPosts();
    expect(posts).toHaveLength(1);
    const [url, init] = posts[0] as [string, RequestInit];
    expect(url).toBe("/api/orders");
    expect(init.method).toBe("POST");
  });
});
