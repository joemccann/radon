/**
 * @vitest-environment jsdom
 *
 * ScannerHero — theta/strength tab switch. Pinned behaviours:
 *  - only the active tab's hook polls (active flag)
 *  - theta rows render struct label, theta/day, credit, dte
 *  - strength rows render the 7-gate ladder with failed cells marked
 *  - meta rail derives counts from the payload
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

import ScannerHero from "@/components/dashboard/ScannerHero";
import type { StrengthConfirmationData, ThetaHarvesterData } from "@/lib/types";

const thetaActive = vi.fn<(active: boolean) => void>();
const strengthActive = vi.fn<(active: boolean) => void>();
let thetaError: string | null = null;
let strengthError: string | null = null;

const THETA: ThetaHarvesterData = {
  scan_time: "2026-08-07T14:00:00Z",
  source: "Unusual Whales",
  universe: "preset:ndx100",
  tickers_scanned: 98,
  candidates_found: 22,
  theta_harvest_count: 4,
  results: [
    {
      ticker: "TXN",
      score: 98.7,
      verdict: "THETA_HARVEST",
      structure: {
        expiry: "20260901",
        dte: 23,
        short_put: { symbol: "TXN", expiry: "20260901", strike: 250, right: "P", iv: 0.3, delta: -0.1, theta: -0.2, gamma: 0, vega: 0, volume: 1, open_interest: 1 },
        short_call: { symbol: "TXN", expiry: "20260901", strike: 320, right: "C", iv: 0.3, delta: 0.1, theta: -0.2, gamma: 0, vega: 0, volume: 1, open_interest: 1 },
        net_delta: 0,
        theta: 41.5,
        gamma: 0,
        vega: 0,
        credit: 6.43,
      },
      spot: 280,
      iv: 0.3,
      hv20: 0.2,
      hv60: 0.2,
      iv_rv_edge: 0.1,
      iv_rv_ratio: 1.5,
      trend_20d_pct: 0,
      range_score: 1,
      dealer_support: "SUPPORT",
      net_gex: null,
      gex_flip: null,
      setup: "",
      gates: {},
      errors: [],
    },
  ],
};

const STRENGTH: StrengthConfirmationData = {
  scan_time: "2026-08-07T14:05:00Z",
  source: "Unusual Whales + Radon regime caches",
  universe: "preset:ndx100",
  tickers_scanned: 98,
  candidates_found: 83,
  confirmed_strength_count: 0,
  results: [
    {
      ticker: "ROST",
      verdict: "WATCHLIST",
      score: 57,
      groups_passed: 4,
      spot: 243.29,
      factors: [
        { group: "Q-SCORES", passed: true, checks_passed: 2, checks_total: 2, source: "APPROX", checks: [], notes: [] },
        { group: "NET GEX", passed: false, checks_passed: 0, checks_total: 2, source: "UW", checks: [], notes: [] },
        { group: "CALL POSITIONING", passed: true, checks_passed: 2, checks_total: 2, source: "UW", checks: [], notes: [] },
        { group: "TERM STRUCTURE", passed: true, checks_passed: 2, checks_total: 2, source: "UW", checks: [], notes: [] },
        { group: "VOLATILITY SMILE", passed: true, checks_passed: 2, checks_total: 2, source: "UW", checks: [], notes: [] },
        { group: "SYSTEMATIC POSITIONING", passed: false, checks_passed: 0, checks_total: 2, source: "APPROX", checks: [], notes: [] },
        { group: "MARKET BREADTH", passed: false, checks_passed: 0, checks_total: 1, source: "UW", checks: [], notes: [] },
      ],
      errors: [],
    },
  ],
};

vi.mock("@/lib/useThetaHarvester", () => ({
  useThetaHarvester: (active: boolean) => {
    thetaActive(active);
    return { data: thetaError ? null : active ? THETA : null, loading: false, syncing: false, error: thetaError, lastSync: null, syncNow: vi.fn() };
  },
}));

vi.mock("@/lib/useStrengthConfirmation", () => ({
  useStrengthConfirmation: (active: boolean) => {
    strengthActive(active);
    return { data: strengthError ? null : active ? STRENGTH : null, loading: false, syncing: false, error: strengthError, lastSync: null, syncNow: vi.fn() };
  },
}));

afterEach(() => {
  cleanup();
  thetaActive.mockClear();
  strengthActive.mockClear();
  thetaError = null;
  strengthError = null;
});

describe("ScannerHero", () => {
  it("starts on theta with only the theta hook active", () => {
    render(<ScannerHero />);
    expect(thetaActive).toHaveBeenLastCalledWith(true);
    expect(strengthActive).toHaveBeenLastCalledWith(false);
    expect(screen.getByText("SHORT 250P / 320C")).toBeTruthy();
    expect(screen.getByText("+41.50/d")).toBeTruthy();
    expect(screen.getByText("$6.43")).toBeTruthy();
    expect(screen.getByText("23")).toBeTruthy();
  });

  it("switching to strength activates only the strength hook", () => {
    render(<ScannerHero />);
    fireEvent.click(screen.getByRole("button", { name: /7-step strength/i }));
    expect(strengthActive).toHaveBeenLastCalledWith(true);
    expect(thetaActive).toHaveBeenLastCalledWith(false);
    expect(screen.getByText("ROST")).toBeTruthy();
  });

  it("renders the 7-gate ladder with failed cells marked", () => {
    render(<ScannerHero />);
    fireEvent.click(screen.getByRole("button", { name: /7-step strength/i }));
    const cells = document.querySelectorAll(".gate-ladder__cell");
    expect(cells).toHaveLength(7);
    const failed = document.querySelectorAll(".gate-ladder__cell--fail");
    expect(failed).toHaveLength(3);
    expect(screen.getByText("4")).toBeTruthy(); // passed count
    expect(screen.getByText("PARTIAL")).toBeTruthy();
  });

  it("derives the meta rail from the payload", () => {
    render(<ScannerHero />);
    const rail = document.querySelector(".panel-meta-rail");
    expect(rail?.textContent).toContain("98");
    expect(rail?.textContent).toContain("22");
  });

  it("links OPEN SCANNER to /scanner", () => {
    render(<ScannerHero />);
    const link = screen.getByRole("link", { name: /open scanner/i }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/scanner");
  });

  it("distinguishes a scanner failure from an empty successful scan", () => {
    thetaError = "Theta scanner unavailable";
    render(<ScannerHero />);
    expect(screen.getByRole("alert").textContent).toContain("Theta scanner unavailable");
    expect(screen.queryByText(/No theta candidates/)).toBeNull();
  });
});
