/**
 * @vitest-environment jsdom
 */

import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import VcgPanel from "../components/VcgPanel";

const mockUseVcg = vi.fn();

vi.mock("@/lib/useVcg", () => ({
  useVcg: (...args: unknown[]) => mockUseVcg(...args),
}));

function vcgHookState(scanTime: string) {
  return {
    data: {
      scan_time: scanTime,
      market_open: true,
      credit_proxy: "HYG",
      signal: {
        vcg: 3.15,
        vcg_adj: 3.15,
        residual: 0.006132,
        beta1_vvix: -0.013941,
        beta2_vix: -0.023025,
        alpha: 0,
        vix: 26.15,
        vvix: 122.82,
        credit_price: 79.44,
        credit_5d_return_pct: -0.01,
        ro: 0,
        edr: 1,
        tier: 3,
        bounce: 0,
        vvix_severity: "extreme",
        sign_ok: true,
        sign_suppressed: false,
        pi_panic: 0,
        regime: "DIVERGENCE",
        interpretation: "EDR",
        attribution: {
          vvix_pct: 41,
          vix_pct: 59,
          vvix_component: 0,
          vix_component: 0,
          model_implied: 0,
        },
      },
      history: [],
    },
    loading: false,
    error: null,
    lastSync: scanTime,
  };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("VcgPanel — freshness rail", () => {
  it("shows the ET session of scan_time and counts down to the next 5-minute ET slot", () => {
    // 18:07 UTC on a Wednesday is 14:07 ET: three minutes short of
    // radon-vcg-refresh.timer's 14:10 ET slot. The plain data_refresh
    // constant (CRI / GEX) would read 8m 00s here.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-02T18:07:00Z"));
    mockUseVcg.mockReturnValue(vcgHookState("2026-09-02T18:00:00Z"));
    render(React.createElement(VcgPanel, { prices: {} }));
    act(() => {
      vi.advanceTimersByTime(0);
    });
    const rail = screen.getByTestId("vcg-freshness-rail");
    expect(rail).toBeTruthy();
    expect(rail.textContent).toContain("2026-09-02");
    expect(screen.getByTestId("vcg-freshness-rail-countdown").textContent).toBe("3m 00s");
    expect(rail.textContent).toContain("Next sample");
  });

  it("falls back to the last history row when scan_time is unparseable", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-02T18:07:00Z"));
    const state = vcgHookState("not-a-timestamp");
    state.data.history = [
      { date: "2026-09-01", residual: 0.01, vcg: 1.2, vcg_adj: 1.2, beta1: -0.01, beta2: -0.02, vix: 18.1, vvix: 96.4, credit: 79.9 },
    ];
    mockUseVcg.mockReturnValue(state);
    render(React.createElement(VcgPanel, { prices: {} }));
    act(() => {
      vi.advanceTimersByTime(0);
    });
    expect(screen.getByTestId("vcg-freshness-rail").textContent).toContain("2026-09-01");
  });
});

describe("VcgPanel EDR badge", () => {
  it("styles the EDR chip with the warning design token", () => {
    mockUseVcg.mockReturnValue({
      data: {
        scan_time: "2026-03-24T06:42:00Z",
        market_open: true,
        credit_proxy: "HYG",
        signal: {
          vcg: 3.15,
          vcg_adj: 3.15,
          residual: 0.006132,
          beta1_vvix: -0.013941,
          beta2_vix: -0.023025,
          alpha: 0,
          vix: 26.15,
          vvix: 122.82,
          credit_price: 79.44,
          credit_5d_return_pct: -0.01,
          ro: 0,
          edr: 1,
          tier: 3,
          bounce: 0,
          vvix_severity: "extreme",
          sign_ok: true,
          sign_suppressed: false,
          pi_panic: 0,
          regime: "DIVERGENCE",
          interpretation: "EDR",
          attribution: {
            vvix_pct: 41,
            vix_pct: 59,
            vvix_component: 0,
            vix_component: 0,
            model_implied: 0,
          },
        },
        history: [],
      },
      loading: false,
      error: null,
      lastSync: "2026-03-24T06:42:00Z",
    });

    const { container } = render(React.createElement(VcgPanel, { prices: {} }));

    const edrBadge = Array.from(container.querySelectorAll(".section-header .pill"))
      .find((node) => node.textContent?.trim() === "EDR");
    expect(edrBadge).toBeTruthy();
    expect(edrBadge.getAttribute("style")).toContain("var(--warning)");
  });
});
