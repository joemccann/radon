/**
 * @vitest-environment jsdom
 *
 * GRG freshness badge (data-testid="grg-freshness-badge") priority:
 *   syncing                  -> spinner + "SYNCING" (grg-status-badge-syncing)
 *   isGammaRotationStale     -> "STALE"   (grg-status-badge-stale)
 *   data.market_open         -> "LIVE"    (grg-status-badge-live)
 *   otherwise                -> "FRESH"   (grg-status-badge-fresh)
 *
 * GammaRotationBody is not exported, so we render the default GammaRotationPanel
 * and drive state via a mocked useGammaRotation. The badge calls
 * isGammaRotationStale(data) directly; rather than craft fragile scan_time /
 * session fixtures that depend on the wall clock, we mock
 * @/lib/gammaRotationStaleness so each case controls staleness deterministically.
 * The payload's own market_open flag still drives the LIVE vs FRESH branch.
 */

import React from "react";
import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import GammaRotationPanel from "../components/GammaRotationPanel";

const mockUseGammaRotation = vi.fn();
const mockIsGammaRotationStale = vi.fn();

vi.mock("@/lib/useGammaRotation", () => ({
  useGammaRotation: (...args: unknown[]) => mockUseGammaRotation(...args),
}));

vi.mock("@/lib/useMarketHours", () => ({
  MarketState: { OPEN: "OPEN", CLOSED: "CLOSED", EXTENDED: "EXTENDED" },
}));

vi.mock("@/lib/gammaRotationStaleness", () => ({
  isGammaRotationStale: (...args: unknown[]) => mockIsGammaRotationStale(...args),
}));

function makeGrgData(marketOpen: boolean) {
  return {
    scan_time: "2026-05-31T15:00:00Z",
    market_open: marketOpen,
    data_date: "2026-05-29",
    source: "Unusual Whales",
    storage: "turso",
    lookback_days: 250,
    z_window: 63,
    signal: {
      state: "RISK_ON_DIVERGENCE",
      state_label: "Risk-on divergence",
      interpretation: "TOP_WATCH",
      tier: 2,
      top_watch: true,
      bottom_watch: false,
      top_score: 4,
      bottom_score: 1,
      grg_z: 2.68,
      raw_spread: 3.12,
      spy_gamma_z: 1.84,
      tlt_gamma_z: -1.67,
      spy_3d_gamma_change: -1000,
      tlt_3d_gamma_change: -500,
      summary: "SPY gamma cushioning equities while TLT gamma amplifies duration moves.",
    },
    assets: {
      SPY: {
        ticker: "SPY",
        spot: 590,
        data_date: "2026-05-29",
        strike_data_date: "2026-05-29",
        net_gamma: 836147.5,
        net_gex: 836147.5,
        call_gex: 4047846.7,
        put_gex: -3211699.2,
        net_delta: 177651415,
        gamma_z: 1.84,
        gamma_1d_change: 2000,
        gamma_3d_change: -1000,
        state: "CUSHION",
        spot_vs_flip_pct: 1.2,
        levels: { gex_flip: { strike: 583, gamma: 0, distance: -7, distance_pct: -1.2 } },
      },
      TLT: {
        ticker: "TLT",
        spot: 91,
        data_date: "2026-05-29",
        strike_data_date: "2026-05-29",
        net_gamma: -721000,
        net_gex: -721000,
        call_gex: 100,
        put_gex: -721100,
        net_delta: 123,
        gamma_z: -1.67,
        gamma_1d_change: -2000,
        gamma_3d_change: -500,
        state: "WHIP",
        spot_vs_flip_pct: -0.8,
        levels: { gex_flip: { strike: 92, gamma: 0, distance: 1, distance_pct: 1.1 } },
      },
    },
    gates: [
      { id: "polarity", label: "Polarity", status: "PASS", copy: "Clean risk-on divergence." },
    ],
    history: [
      { date: "2026-05-29", spy_net_gamma: 3, tlt_net_gamma: -3, spy_gamma_z: 1.84, tlt_gamma_z: -1.67, grg_z: 2.68, raw_spread: 3.51, state: "RISK_ON_DIVERGENCE" },
    ],
    top_bottom: {
      top: { active: true, copy: "Potential top copy." },
      bottom: { active: false, copy: "Potential bottom copy." },
    },
  };
}

function renderWith({
  marketOpen,
  syncing,
  stale,
}: {
  marketOpen: boolean;
  syncing: boolean;
  stale: boolean;
}) {
  mockIsGammaRotationStale.mockReturnValue(stale);
  mockUseGammaRotation.mockReturnValue({
    data: makeGrgData(marketOpen),
    loading: false,
    error: null,
    lastSync: "2026-05-31T15:00:00Z",
    syncing,
  });
  const { container } = render(<GammaRotationPanel />);
  return container.querySelector("[data-testid='grg-freshness-badge']");
}

describe("GRG freshness badge", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders SYNCING when syncing is true (highest priority)", () => {
    // syncing wins even though market is open and data is stale.
    const badge = renderWith({ marketOpen: true, syncing: true, stale: true });
    expect(badge).toBeTruthy();
    expect(badge?.textContent?.trim()).toBe("SYNCING");
    expect(badge?.getAttribute("aria-busy")).toBe("true");
    expect(badge?.className).toContain("grg-status-badge-syncing");
    expect(badge?.className).toContain("regime-sync-status");
    expect(badge?.querySelector('[data-testid="regime-sync-working-icon"]')).toBeTruthy();
  });

  it("renders FRESH for market-closed, same-session, non-stale data", () => {
    const badge = renderWith({ marketOpen: false, syncing: false, stale: false });
    expect(badge?.textContent?.trim()).toBe("FRESH");
    expect(badge?.className).toContain("grg-status-badge-fresh");
  });

  it("renders LIVE when market is open and data is fresh", () => {
    const badge = renderWith({ marketOpen: true, syncing: false, stale: false });
    expect(badge?.textContent?.trim()).toBe("LIVE");
    expect(badge?.className).toContain("grg-status-badge-live");
  });

  it("renders STALE when staleness wins over the LIVE branch", () => {
    const badge = renderWith({ marketOpen: true, syncing: false, stale: true });
    expect(badge?.textContent?.trim()).toBe("STALE");
    expect(badge?.className).toContain("grg-status-badge-stale");
  });
});
