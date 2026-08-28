/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkspaceSections, { discoverOrderHref } from "../components/WorkspaceSections";
import type { DiscoverCandidate } from "../lib/types";

const replaceMock = vi.hoisted(() => vi.fn());
const searchParamsMock = vi.hoisted(() => vi.fn(() => new URLSearchParams("")));
const discoverCandidatesMock = vi.hoisted(() => ({ current: [] as DiscoverCandidate[] }));
const viewportMock = vi.hoisted(() => ({ isMobile: false, hasMounted: true }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/scanner",
  useRouter: () => ({ replace: replaceMock }),
  useSearchParams: searchParamsMock,
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: viewportMock.isMobile, hasMounted: viewportMock.hasMounted }),
}));

vi.mock("@/lib/useTickerNav", () => ({
  useTickerNav: () => ({
    navigateToTicker: vi.fn(),
  }),
}));

vi.mock("@/lib/useScanner", () => ({
  useScanner: () => ({
    data: {
      scan_time: "2026-06-24T15:00:00Z",
      tickers_scanned: 2,
      signals_found: 1,
      top_signals: [
        {
          ticker: "AAPL",
          signal: "STRONG",
          direction: "ACCUMULATION",
          score: 91.2,
          strength: 82.5,
          buy_ratio: 0.71,
          sustained_days: 3,
          num_prints: 48,
        },
      ],
    },
    syncing: false,
    error: null,
    lastSync: "2026-06-24T15:00:00Z",
    syncNow: vi.fn(),
  }),
}));

vi.mock("@/lib/useDiscover", () => ({
  useDiscover: () => ({
    data: {
      discovery_time: "2026-06-24T15:05:00Z",
      alerts_analyzed: 7,
      candidates_found: discoverCandidatesMock.current.length,
      candidates: discoverCandidatesMock.current,
    },
    loading: false,
    syncing: false,
    error: null,
    lastSync: "2026-06-24T15:05:00Z",
    syncNow: vi.fn(),
  }),
}));

function discoverCandidate(overrides: Partial<DiscoverCandidate> = {}): DiscoverCandidate {
  return {
    ticker: "MSFT",
    score: 72.5,
    score_breakdown: {},
    alerts: 3,
    total_premium: 1_250_000,
    calls: 8,
    puts: 1,
    options_bias: "BULLISH",
    sweeps: 2,
    avg_vol_oi: 4.2,
    sector: "Technology",
    issue_type: "Common Stock",
    dp_direction: "ACCUMULATION",
    dp_strength: 64.1,
    dp_buy_ratio: 0.68,
    dp_sustained_days: 2,
    dp_total_prints: 19,
    confluence: true,
    ...overrides,
  };
}

vi.mock("@/lib/useThetaHarvester", () => ({
  useThetaHarvester: () => ({
    data: null,
    loading: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
  }),
}));

vi.mock("@/lib/useStrengthConfirmation", () => ({
  useStrengthConfirmation: () => ({
    data: null,
    loading: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
  }),
}));

beforeEach(() => {
  discoverCandidatesMock.current = [discoverCandidate()];
  viewportMock.isMobile = false;
  viewportMock.hasMounted = true;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  searchParamsMock.mockReturnValue(new URLSearchParams(""));
});

describe("Scanner flow header tooltips", () => {
  it("renders help bubbles for the flow scanner metric headers", () => {
    render(<WorkspaceSections section="scanner" />);

    const expectations = [
      ["signal", "Flow intensity bucket"],
      ["direction", "Dominant institutional flow direction"],
      ["score", "Composite flow score"],
      ["strength", "Raw dark-pool flow strength"],
      ["buy-ratio", "Share of prints"],
      ["sustained", "Number of sessions"],
      ["prints", "Number of dark-pool transactions"],
    ] as const;

    for (const [key, expectedText] of expectations) {
      const trigger = screen.getByTestId(`scanner-header-tooltip-${key}`);
      fireEvent.mouseEnter(trigger);
      expect(screen.getByTestId(`scanner-header-tooltip-content-${key}`).textContent).toContain(expectedText);
      fireEvent.mouseLeave(trigger);
    }
  });

  it("renders discover candidates as a scanner mode", () => {
    searchParamsMock.mockReturnValue(new URLSearchParams("mode=discover"));

    render(<WorkspaceSections section="scanner" />);

    expect(screen.getByRole("tab", { name: "Flow Signals" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Theta Harvester" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "7-Step Strength" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Discover" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("Discovery Candidates")).toBeTruthy();
    expect(screen.getByText("MSFT")).toBeTruthy();
    expect(screen.getByText("ACCUMULATION")).toBeTruthy();
    expect(screen.getByText("BULLISH")).toBeTruthy();
  });
});

describe("Discover order links", () => {
  function renderDiscover() {
    searchParamsMock.mockReturnValue(new URLSearchParams("mode=discover"));
    return render(<WorkspaceSections section="scanner" />);
  }

  function orderLinks(container: HTMLElement): HTMLAnchorElement[] {
    return Array.from(container.querySelectorAll<HTMLAnchorElement>('a[data-testid^="discover-order-link-"]'));
  }

  it("builds a chain deck href from the ticker alone", () => {
    expect(discoverOrderHref(discoverCandidate({ ticker: "googl" }))).toBe("/GOOGL?deck=c&src=discover");
  });

  it("returns null when the row cannot address a ticker", () => {
    expect(discoverOrderHref(discoverCandidate({ ticker: "   " }))).toBeNull();
    expect(discoverOrderHref(discoverCandidate({ ticker: "" }))).toBeNull();
  });

  it("links the desktop discover ticker cell into the chain order builder", () => {
    const { container } = renderDiscover();

    expect(screen.getByTestId("discover-order-link-MSFT").getAttribute("href")).toBe("/MSFT?deck=c&src=discover");
    expect(orderLinks(container)).toHaveLength(1);
  });

  it("omits the order link for a row whose ticker is blank", () => {
    discoverCandidatesMock.current = [discoverCandidate({ ticker: "   " }), discoverCandidate({ ticker: "XLE" })];

    const { container } = renderDiscover();

    const links = orderLinks(container);
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("/XLE?deck=c&src=discover");
  });

  it("links mobile discover cards to the same chain order builder href", () => {
    viewportMock.isMobile = true;

    const { container } = renderDiscover();

    expect(screen.getByTestId("mobile-discover-list")).toBeTruthy();
    expect(screen.getByTestId("discover-order-link-MSFT").getAttribute("href")).toBe("/MSFT?deck=c&src=discover");
    expect(orderLinks(container)).toHaveLength(1);
  });
});
