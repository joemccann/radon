/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import WorkspaceSections from "../components/WorkspaceSections";

const replaceMock = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  usePathname: () => "/scanner",
  useRouter: () => ({ replace: replaceMock }),
  useSearchParams: () => new URLSearchParams(""),
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true }),
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
});
