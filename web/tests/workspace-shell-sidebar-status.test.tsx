/**
 * @vitest-environment jsdom
 */

import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import WorkspaceShell from "../components/WorkspaceShell";

const sidebarSpy = vi.fn();

vi.mock("next/dynamic", () => ({
  default: () => () => null,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/orders",
}));

vi.mock("../lib/usePortfolio", () => ({
  usePortfolio: () => ({
    data: { bankroll: 100_000, positions: [], account_summary: {}, exposure: {}, violations: [] },
    syncing: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
  }),
}));

vi.mock("../lib/useOrders", () => ({
  useOrders: () => ({
    data: {
      last_sync: "2026-04-05T10:36:31Z",
      open_orders: [],
      executed_orders: [],
      open_count: 0,
      executed_count: 0,
    },
    syncing: false,
    error: null,
    lastSync: "2026-04-05T10:36:31Z",
    syncNow: vi.fn(),
    updateData: vi.fn(),
  }),
}));

vi.mock("../lib/useMarketHours", () => ({
  MarketState: { CLOSED: "CLOSED" },
  useMarketHours: () => "CLOSED",
}));

vi.mock("../lib/useToast", () => ({
  useToast: () => ({
    toasts: [],
    addToast: vi.fn(),
    removeToast: vi.fn(),
  }),
}));

vi.mock("../lib/OrderActionsContext", () => ({
  useOrderActions: () => ({
    drainNotifications: () => [],
    setOrdersUpdater: vi.fn(),
  }),
}));

vi.mock("../lib/usePrices", () => ({
  usePrices: () => ({
    prices: {},
    fundamentals: {},
    connected: false,
    ibConnected: false,
    ibIssue: null,
    ibStatusMessage: null,
  }),
}));

vi.mock("../lib/useIBStatus", () => ({
  useIBStatus: () => ({
    wsConnected: true,
    ibConnected: true,
    disconnectedSince: null,
    connectionState: "connected",
  }),
}));

vi.mock("../lib/usePreviousClose", () => ({
  usePreviousClose: (prices: unknown) => prices,
}));

vi.mock("../lib/TickerDetailContext", () => ({
  useTickerDetail: () => ({
    chainContracts: [],
    setActiveTicker: vi.fn(),
    setPrices: vi.fn(),
    setFundamentals: vi.fn(),
    setPortfolio: vi.fn(),
    setOrders: vi.fn(),
  }),
}));

vi.mock("../components/Sidebar", () => ({
  default: (props: { ibConnected?: boolean }) => {
    sidebarSpy(props);
    return <div data-testid="sidebar-status">{props.ibConnected ? "CONNECTED" : "OFFLINE"}</div>;
  },
}));

vi.mock("../components/Header", () => ({
  default: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock("../components/ChatPanel", () => ({
  default: () => null,
}));

vi.mock("../components/MetricCards", () => ({
  default: () => null,
}));

vi.mock("../components/ConnectionBanner", () => ({
  default: () => null,
}));

vi.mock("../components/FlexTokenBanner", () => ({
  default: () => null,
}));

vi.mock("../components/Toast", () => ({
  default: () => null,
}));

describe("WorkspaceShell sidebar IB status", () => {
  beforeEach(() => {
    sidebarSpy.mockClear();
    cleanup();
    localStorage.clear();
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockReturnValue({
        matches: true,
        media: "(prefers-color-scheme: dark)",
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    });
  });

  it("keeps the sidebar connected on an empty orders page when the shared IB status socket is healthy", () => {
    render(<WorkspaceShell section="orders" />);

    expect(screen.getByTestId("sidebar-status").textContent).toContain("CONNECTED");
    expect(sidebarSpy).toHaveBeenCalled();
    expect(sidebarSpy.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({ ibConnected: true }),
    );
  });
});
