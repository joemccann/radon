/**
 * @vitest-environment jsdom
 *
 * FU3 — mount deferred panels + finish nav.
 *
 * Behaviours pinned:
 *  - DashboardSurface mounts <CatalystCard/> (Upcoming Catalysts surface).
 *  - The flow-analysis surface mounts <InformedFlowPanel ticker={...}/> for the
 *    active ticker (Congress & Insider Activity surface).
 *  - The alerts page renders <AlertsPanel/> (Signal Alert Rules surface).
 *  - WorkspaceShell navItems include reachable Alerts + Workflow entries.
 *
 * Data hooks are mocked so no live UW/IB/Turso/network is touched.
 */
import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";

// ── Stub the heavy dashboard siblings so the render isolates the
//    CatalystsQuadrant mount. (FeedPanel/DashboardNewsFeed pull in the app
//    router; ScannerHero/EngineStatePanel pull in their own hooks.) ──
vi.mock("@/components/DashboardNewsFeed", () => ({
  default: () => React.createElement("div", { "data-testid": "mock-news-feed" }),
}));
vi.mock("@/components/dashboard/FeedPanel", () => ({
  default: () => React.createElement("div", { "data-testid": "mock-feed-panel" }),
}));
vi.mock("@/components/dashboard/ScannerHero", () => ({
  default: () => React.createElement("div", { "data-testid": "mock-scanner-hero" }),
}));
vi.mock("@/components/dashboard/EngineStatePanel", () => ({
  default: () => React.createElement("div", { "data-testid": "mock-engine-panel" }),
}));

// ── Mock the data hooks the mounted panels depend on ──
vi.mock("@/lib/useCatalysts", () => {
  // Window-relative fixture date: CatalystCard drops past events at render
  // time, so a hardcoded date would rot out of the card as the clock advances.
  const fixtureDate = new Date(Date.now() + 2 * 86_400_000).toISOString().slice(0, 10);
  return {
    useCatalysts: () => ({
      data: {
        scan_time: new Date().toISOString(),
        count: 1,
        catalysts: [
          { ticker: "AAPL", type: "earnings", title: "APPLE INC", date: fixtureDate, source: "earnings_premarket", days_until: 2 },
        ],
      },
      isLoading: false,
      error: null,
      refresh: vi.fn(),
    }),
  };
});

vi.mock("@/lib/useInformedFlow", () => ({
  useInformedFlow: () => ({
    data: {
      scan_time: "2026-06-21T12:00:00Z",
      congress_trades: [
        { side: "BUY", person: "Rep. Doe", amount: "$50K", date: "2026-06-18", days_ago: 3 },
      ],
      insider_trades: [],
      institutional_summary: null,
    },
    isLoading: false,
    error: null,
  }),
}));

// Stub the flow surface siblings so rendering the flow-analysis section
// isolates the InformedFlowPanel mount (the input pulls in the app router; the
// report pulls in its own scan hook — neither is under test here).
vi.mock("@/components/flow-analysis/FlowAnalysisTickerInput", () => ({
  default: () => React.createElement("div", { "data-testid": "mock-flow-input" }),
}));
vi.mock("@/components/flow-analysis/TickerFlowReport", () => ({
  default: () => React.createElement("div", { "data-testid": "mock-flow-report" }),
}));

vi.mock("@/lib/useAlerts", () => ({
  useAlerts: () => ({
    rules: [{ id: "r1", ticker: "AAPL", metric: "flow_strength", op: ">", threshold: 70, channel: "discord" }],
    isLoading: false,
    error: null,
    createRule: vi.fn(),
    deleteRule: vi.fn(),
  }),
}));

import DashboardSurface from "@/components/dashboard/DashboardSurface";
import { AlertsPanel } from "@/components/alerts/AlertsPanel";
import WorkspaceSections from "@/components/WorkspaceSections";
import { navItems } from "@/lib/data";

afterEach(() => {
  cleanup();
});

describe("FU3 panel mounts", () => {
  it("DashboardSurface mounts the CatalystsQuadrant surface", async () => {
    render(<DashboardSurface portfolio={null} marketState={null} />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 2, name: /upcoming catalysts/i })).toBeTruthy(),
    );
    expect(screen.getByRole("heading", { level: 3, name: /upcoming/i })).toBeTruthy();
    expect(screen.getByText(/APPLE INC/)).toBeTruthy();
  });

  it("flow-analysis surface mounts InformedFlowPanel for the active ticker", async () => {
    render(<WorkspaceSections section="flow-analysis" tickerParam="AAPL" />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /congress.*insider activity/i })).toBeTruthy(),
    );
  });

  it("alerts surface mounts the AlertsPanel via WorkspaceSections", async () => {
    render(<WorkspaceSections section="alerts" />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /signal alert rules/i })).toBeTruthy(),
    );
  });
});

describe("FU3 nav wiring", () => {
  it("navItems includes a reachable Alerts entry", () => {
    const alerts = navItems.find((i) => i.route === "alerts");
    expect(alerts).toBeTruthy();
    expect(alerts?.href).toBe("/alerts");
    expect(alerts?.hidden).not.toBe(true);
  });

  it("navItems includes a reachable Workflow entry", () => {
    const workflow = navItems.find((i) => i.route === "workflow");
    expect(workflow).toBeTruthy();
    expect(workflow?.href).toBe("/workflow");
    expect(workflow?.hidden).not.toBe(true);
  });
});
