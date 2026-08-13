/**
 * @vitest-environment jsdom
 */

import React from "react";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import GammaRotationPanel from "../components/GammaRotationPanel";

const mockUseGammaRotation = vi.fn();

vi.mock("@/lib/useGammaRotation", () => ({
  useGammaRotation: (...args: unknown[]) => mockUseGammaRotation(...args),
}));

vi.mock("@/lib/useMarketHours", () => ({
  MarketState: { OPEN: "OPEN", CLOSED: "CLOSED", EXTENDED: "EXTENDED" },
}));

const MOCK_GRG = {
  scan_time: "2026-05-31T15:00:00Z",
  market_open: false,
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
    summary: "SPY gamma is cushioning equities while TLT gamma is amplifying duration moves.",
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
    { id: "polarity", label: "Polarity", status: "PASS", copy: "SPY positive and TLT negative identifies the clean risk-on divergence." },
    { id: "decay", label: "Decay", status: "PASS", copy: "A negative 3-session SPY gamma slope marks possible equity cushion decay." },
  ],
  history: [
    { date: "2026-05-27", spy_net_gamma: 1, tlt_net_gamma: -1, spy_gamma_z: 0.5, tlt_gamma_z: -0.5, grg_z: 1.1, raw_spread: 1, state: "RISK_ON_DIVERGENCE" },
    { date: "2026-05-28", spy_net_gamma: 2, tlt_net_gamma: -2, spy_gamma_z: 1.2, tlt_gamma_z: -1.1, grg_z: 2.1, raw_spread: 2.3, state: "RISK_ON_DIVERGENCE" },
    { date: "2026-05-29", spy_net_gamma: 3, tlt_net_gamma: -3, spy_gamma_z: 1.84, tlt_gamma_z: -1.67, grg_z: 2.68, raw_spread: 3.51, state: "RISK_ON_DIVERGENCE" },
  ],
  top_bottom: {
    top: { active: true, copy: "Potential top copy." },
    bottom: { active: false, copy: "Potential bottom copy." },
  },
};

describe("GammaRotationPanel", () => {
  it("renders loading state", () => {
    mockUseGammaRotation.mockReturnValue({ data: null, loading: true, error: null });
    const { container } = render(<GammaRotationPanel />);
    expect(container.textContent).toContain("Sampling SPY and TLT gamma rotation");
  });

  it("renders the GRG signal, assets, gates, and chart", () => {
    mockUseGammaRotation.mockReturnValue({ data: MOCK_GRG, loading: false, error: null });
    const { container } = render(<GammaRotationPanel />);
    expect(container.textContent).toContain("Gamma Rotation Gap");
    expect(container.textContent).toContain("+2.68σ");
    expect(container.textContent).toContain("TOP WATCH");
    expect(container.textContent).toContain("SPY GEX");
    expect(container.textContent).toContain("TLT GEX");
    expect(container.textContent).toContain("Top identification");
    expect(container.querySelector("[data-testid='grg-chart']")).toBeTruthy();
  });

  it("breaks paths at missing observations and labels an expanded z-score domain", () => {
    const data = {
      ...MOCK_GRG,
      history: [
        { ...MOCK_GRG.history[0], grg_z: 4.5 },
        { ...MOCK_GRG.history[1], grg_z: null },
        { ...MOCK_GRG.history[2], grg_z: 2.5 },
      ],
    };
    mockUseGammaRotation.mockReturnValue({ data, loading: false, error: null });
    const { container } = render(<GammaRotationPanel />);

    const path = container.querySelector('path[stroke="var(--warning)"]')?.getAttribute("d") ?? "";
    expect(path.match(/M/g)).toHaveLength(2);
    expect(path).not.toContain("NaN");
    expect(container.textContent).toContain("+4.5σ");
  });
});
