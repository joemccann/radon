/**
 * @vitest-environment jsdom
 *
 * ScannerHero — theta/vol-cone tab switch. Pinned behaviours:
 *  - only the active tab's hook polls (active flag)
 *  - theta rows render struct label, theta/day, credit, dte
 *  - vol-cone rows render the cheap-cone hits with expiry, ATM IV and regime
 *  - meta rail derives counts from the payload
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

import ScannerHero from "@/components/dashboard/ScannerHero";
import type { ThetaHarvesterData } from "@/lib/types";
import type { VolConeData, VolConeName } from "@/lib/volCone";

const thetaActive = vi.fn<(active: boolean) => void>();
const coneActive = vi.fn<(active: boolean) => void>();
let thetaError: string | null = null;
let coneError: string | null = null;

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

function coneName(overrides: Partial<VolConeName> = {}): VolConeName {
  return {
    ticker: "NKE",
    spot: 72.4,
    expiry: "2026-09-18",
    month: "SEP",
    dte: 24,
    atm_iv: 0.28,
    call_10_iv: 0.3,
    put_10_iv: 0.31,
    call_10_strike: 79.6,
    put_10_strike: 65.2,
    p10: 0.26,
    p90: 0.46,
    atm_percentile: 0.04,
    call_10_percentile: 0.05,
    put_10_percentile: 0.07,
    wing_score: 0.06,
    regime: "CHEAP_WINGS",
    series: [],
    ...overrides,
  };
}

const HITS = [
  coneName(),
  coneName({
    ticker: "KO",
    regime: "CHEAP_ATM",
    expiry: "2026-10-16",
    month: "OCT",
    dte: 52,
    atm_iv: 0.34,
    wing_score: 0.41,
    atm_percentile: 0.11,
  }),
];

const CONE: VolConeData = {
  scan_time: "2026-08-07T14:05:00Z",
  source_as_of: "2026-08-06",
  count: 118,
  hit_count: 2,
  current: HITS[0],
  names: HITS,
  hits: HITS,
};

vi.mock("@/lib/useThetaHarvester", () => ({
  useThetaHarvester: (active: boolean) => {
    thetaActive(active);
    return { data: thetaError ? null : active ? THETA : null, loading: false, syncing: false, error: thetaError, lastSync: null, syncNow: vi.fn() };
  },
}));

let coneData: VolConeData = CONE;

vi.mock("@/lib/useVolCone", () => ({
  useVolCone: (active: boolean) => {
    coneActive(active);
    return { data: coneError ? null : active ? coneData : null, loading: false, syncing: false, error: coneError, lastSync: null, syncNow: vi.fn() };
  },
}));

function openCone() {
  fireEvent.click(screen.getByRole("button", { name: /vol cone/i }));
}

afterEach(() => {
  cleanup();
  thetaActive.mockClear();
  coneActive.mockClear();
  thetaError = null;
  coneError = null;
  coneData = CONE;
});

describe("ScannerHero", () => {
  it("starts on theta with only the theta hook active", () => {
    render(<ScannerHero />);
    expect(thetaActive).toHaveBeenLastCalledWith(true);
    expect(coneActive).toHaveBeenLastCalledWith(false);
    expect(screen.getByText("SHORT 250P / 320C")).toBeTruthy();
    expect(screen.getByText("+41.50/d")).toBeTruthy();
    expect(screen.getByText("$6.43")).toBeTruthy();
    expect(screen.getByText("23")).toBeTruthy();
  });

  it("switching to vol cone activates only the vol-cone hook", () => {
    render(<ScannerHero />);
    openCone();
    expect(coneActive).toHaveBeenLastCalledWith(true);
    expect(thetaActive).toHaveBeenLastCalledWith(false);
    expect(screen.getByText("NKE")).toBeTruthy();
    expect(screen.getByText("KO")).toBeTruthy();
  });

  it("renders each hit's expiry, ATM IV and regime badge", () => {
    render(<ScannerHero />);
    openCone();
    expect(screen.getByText("$72 · SEP 18 · 24D")).toBeTruthy();
    expect(screen.getByText("$72 · OCT 16 · 52D")).toBeTruthy();
    expect(screen.getByText("28.0")).toBeTruthy(); // ATM IV in vol points
    expect(screen.getByText("34.0")).toBeTruthy();
    expect(screen.getByText("CHEAP WINGS")).toBeTruthy();
    expect(screen.getByText("CHEAP ATM")).toBeTruthy();
  });

  it("links a cheap-wings hit to its prefilled strangle", () => {
    render(<ScannerHero />);
    openCone();
    const href = screen.getByLabelText("Open NKE long 10% OTM strangle").getAttribute("href");
    expect(href).toContain("/NKE?");
    expect(href).toContain("src=vol-cone");
    expect(href).toContain("expiry=2026-09-18");
  });

  it("shows only the cheap-cone hits, never the rich or neutral names", () => {
    coneData = {
      ...CONE,
      hit_count: 0,
      hits: [],
      names: [coneName({ ticker: "MSFT", regime: "RICH" })],
    };
    render(<ScannerHero />);
    openCone();
    expect(screen.queryByText("MSFT")).toBeNull();
    expect(screen.getByText(/No cheap vol cones/)).toBeTruthy();
  });

  it("derives the meta rail from the payload", () => {
    render(<ScannerHero />);
    const rail = document.querySelector(".panel-meta-rail");
    expect(rail?.textContent).toContain("98");
    expect(rail?.textContent).toContain("22");
  });

  it("derives the meta rail from the vol-cone payload once switched", () => {
    render(<ScannerHero />);
    openCone();
    const rail = document.querySelector(".panel-meta-rail");
    expect(rail?.textContent).toContain("118");
    expect(rail?.textContent).toContain("2");
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

  it("surfaces a vol-cone fetch failure as an alert", () => {
    coneError = "Vol cone unavailable";
    render(<ScannerHero />);
    openCone();
    expect(screen.getByRole("alert").textContent).toContain("Vol cone unavailable");
    expect(screen.queryByText(/No cheap vol cones/)).toBeNull();
  });
});
