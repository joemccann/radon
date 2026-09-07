/**
 * @vitest-environment jsdom
 *
 * T-457: demo workspace sync chrome asserted on the RENDERED shell, not on
 * component source text. The old demo-workspace-chrome.test.ts was three
 * `toContain` calls against WorkspaceShell.tsx — a prettier reformat would
 * red it, and a stale duplicate of the asserted string would keep it green
 * while demo users saw the live "Sync from IB Gateway" producer action.
 *
 * This file renders the real WorkspaceShell (data hooks mocked, Header kept
 * as a children passthrough so the sync controls it hosts stay visible) and
 * pins the behaviour: demo mode shows the static "Sample snapshot" label and
 * no IB sync producer action; live mode shows the sync button, proving the
 * absence assertion is not an artifact of the harness hiding the chrome.
 */
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- data-hook / chrome mocks (WorkspaceShell's sync chrome stays real) ----

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("next/dynamic", () => ({ default: () => () => null }));
vi.mock("@/lib/ThemeContext", () => ({ useTheme: () => ({ theme: "dark", toggleTheme: vi.fn() }) }));
vi.mock("@/lib/useViewport", () => ({ useViewport: () => ({ isMobile: false, hasMounted: true }) }));
vi.mock("@/lib/useOrders", () => ({
  useOrders: () => ({
    data: null,
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

// Presentational chrome only. Header is the one exception: WorkspaceShell
// hands it the sync-controls block as children, so the mock must render them.
vi.mock("@/components/Sidebar", () => ({ default: () => null }));
vi.mock("@/components/Header", () => ({
  default: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
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

beforeEach(() => {
  delete process.env.NEXT_PUBLIC_RADON_DEMO;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => ({ positions: [], bankroll: 0, last_sync: "2026-09-05T14:00:00Z" }),
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  delete process.env.NEXT_PUBLIC_RADON_DEMO;
});

async function renderShell() {
  render(<WorkspaceShell section="dashboard" />);
  await act(async () => {});
}

const syncProducerButton = () =>
  screen.queryByRole("button", { name: /sync/i })
  ?? screen.queryByTitle(/from IB Gateway/i);

describe("demo workspace sync chrome", () => {
  it("demo mode shows the static sample label and offers no IB producer action", async () => {
    process.env.NEXT_PUBLIC_RADON_DEMO = "1";
    await renderShell();

    expect(screen.getByText("Sample snapshot")).toBeTruthy();
    expect(syncProducerButton()).toBeNull();
    expect(screen.queryByText(/Sync Now|Syncing/)).toBeNull();
  });

  it("live mode renders the sync producer action this harness would otherwise hide", async () => {
    await renderShell();

    const button = screen.getByTitle("Sync portfolio from IB Gateway");
    expect(button.textContent).toContain("Sync Now");
    expect(screen.queryByText("Sample snapshot")).toBeNull();
  });
});
