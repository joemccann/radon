/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import WorkspaceSections, { flowOrderHref } from "../components/WorkspaceSections";
import type { ScannerSignal } from "../lib/types";

const replaceMock = vi.hoisted(() => vi.fn());
const searchParamsMock = vi.hoisted(() => vi.fn(() => new URLSearchParams("")));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/scanner",
  useRouter: () => ({ replace: replaceMock }),
  useSearchParams: searchParamsMock,
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true }),
}));

vi.mock("@/lib/useTickerNav", () => ({
  useTickerNav: () => ({
    navigateToTicker: vi.fn(),
  }),
}));

function signal(overrides: Partial<ScannerSignal>): ScannerSignal {
  return {
    ticker: "GRRR",
    sector: "Unknown",
    score: 95,
    signal: "STRONG",
    direction: "DISTRIBUTION",
    strength: 100,
    buy_ratio: 0,
    num_prints: 1,
    sustained_days: 0,
    recent_direction: "DISTRIBUTION",
    recent_strength: 100,
    ...overrides,
  };
}

vi.mock("@/lib/useScanner", () => ({
  useScanner: () => ({
    data: {
      scan_time: "2026-08-28T15:00:00Z",
      tickers_scanned: 3,
      signals_found: 1,
      top_signals: [
        {
          ticker: "GRRR",
          sector: "Unknown",
          score: 95,
          signal: "STRONG",
          direction: "DISTRIBUTION",
          strength: 100,
          buy_ratio: 0,
          num_prints: 1,
          sustained_days: 0,
          recent_direction: "DISTRIBUTION",
          recent_strength: 100,
        },
        {
          ticker: "MSFT",
          sector: "Technology",
          score: 0,
          signal: "NONE",
          direction: "UNKNOWN",
          strength: 0,
          buy_ratio: null,
          num_prints: 0,
          sustained_days: 0,
          recent_direction: "UNKNOWN",
          recent_strength: 0,
        },
      ],
    },
    syncing: false,
    error: null,
    lastSync: "2026-08-28T15:00:00Z",
    syncNow: vi.fn(),
  }),
}));

vi.mock("@/lib/useDiscover", () => ({
  useDiscover: () => ({
    data: null,
    loading: false,
    syncing: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
  }),
}));

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

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  searchParamsMock.mockReturnValue(new URLSearchParams(""));
});

describe("flowOrderHref", () => {
  it("addresses the ticker's chain deck with the flow source tag", () => {
    expect(flowOrderHref(signal({}))).toBe("/GRRR?deck=c&src=flow");
  });

  it("uppercases the path segment", () => {
    expect(flowOrderHref(signal({ ticker: "grrr" }))).toBe("/GRRR?deck=c&src=flow");
  });

  it("returns null for a blank ticker", () => {
    expect(flowOrderHref(signal({ ticker: "   " }))).toBeNull();
  });

  it("returns null for rows with no tradable signal", () => {
    expect(flowOrderHref(signal({ signal: "NONE", direction: "UNKNOWN" }))).toBeNull();
    expect(flowOrderHref(signal({ signal: "ERROR", direction: "UNKNOWN" }))).toBeNull();
  });
});

describe("Flow Signals order link", () => {
  it("links an actionable row into the chain order builder", () => {
    render(<WorkspaceSections section="scanner" />);

    const link = screen.getByTestId("flow-order-link-GRRR");
    expect(link.getAttribute("href")).toBe("/GRRR?deck=c&src=flow");
    expect(link.textContent).toBe("GRRR");
  });

  it("omits the link for a row with no tradable signal", () => {
    render(<WorkspaceSections section="scanner" />);

    expect(screen.getByText("MSFT")).toBeTruthy();
    expect(screen.queryByTestId("flow-order-link-MSFT")).toBeNull();
  });
});
