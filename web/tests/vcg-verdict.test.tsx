/**
 * @vitest-environment jsdom
 *
 * VCG: one verdict, one owner. The interpretation used to print under the
 * z-score, as the Signal Detail pill, and next to a header regime pill, while
 * the panic-adj tile said "NO SUPPRESSION" about a different suppression.
 * The headline now owns the verdict and is derived from the payload.
 */

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import VcgPanel from "../components/VcgPanel";

const mockUseVcg = vi.fn();
vi.mock("@/lib/useVcg", () => ({
  useVcg: (...args: unknown[]) => mockUseVcg(...args),
}));

function state(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      scan_time: "2026-10-12T17:00:00Z",
      market_open: true,
      credit_proxy: "HYG",
      signal: {
        vcg: 1.62,
        vcg_adj: 1.62,
        residual: 0.002758,
        beta1_vvix: 0.01,
        beta2_vix: -0.02,
        alpha: 0,
        vix: 15.24,
        vvix: 86.63,
        credit_price: 77.29,
        credit_5d_return_pct: 0.4,
        ro: 0,
        edr: 0,
        tier: null,
        bounce: 0,
        vvix_severity: "normal",
        sign_ok: false,
        sign_suppressed: true,
        pi_panic: 0,
        regime: "DIVERGENCE",
        interpretation: "SUPPRESSED",
        attribution: { vvix_pct: 82, vix_pct: 18, vvix_component: 0, vix_component: 0, model_implied: 0 },
        ...overrides,
      },
      history: [],
    },
    loading: false,
    error: null,
    lastSync: "2026-10-12T17:00:00Z",
  };
}

afterEach(cleanup);

describe("VcgPanel verdict headline", () => {
  it("derives the headline from proxy, interpretation and z-score", () => {
    mockUseVcg.mockReturnValue(state());
    render(<VcgPanel prices={{}} />);
    const h = screen.getByTestId("vcg-verdict");
    expect(h.textContent).toContain("HYG");
    expect(h.textContent).toContain("signal suppressed");
    expect(h.textContent).toContain("+1.62σ");
  });

  it("changes with the data rather than a fixed string", () => {
    mockUseVcg.mockReturnValue(state({ interpretation: "EDR", edr: 1, tier: 3, sign_ok: true, sign_suppressed: false, vcg: 2.31 }));
    render(<VcgPanel prices={{}} />);
    const h = screen.getByTestId("vcg-verdict").textContent ?? "";
    expect(h).toContain("early divergence");
    expect(h).toContain("+2.31σ");
    expect(screen.getByTestId("vcg-verdict-detail").textContent).toContain("Tier 3 active");
  });

  it("states panic adjustment, tier and dominant driver in one line", () => {
    mockUseVcg.mockReturnValue(state());
    render(<VcgPanel prices={{}} />);
    const d = screen.getByTestId("vcg-verdict-detail").textContent ?? "";
    expect(d).toContain("No panic adjustment applied");
    expect(d).toContain("no active severity tier");
    expect(d).toContain("VVIX drives 82% of the read");
  });

  it("prints the interpretation exactly once and drops the contradictory suppression copy", () => {
    mockUseVcg.mockReturnValue(state());
    const { container } = render(<VcgPanel prices={{}} />);
    const signalSections = Array.from(container.querySelectorAll(".section")).slice(0, 2);
    const text = signalSections.map((s) => s.textContent ?? "").join(" ");
    expect(text.match(/SUPPRESSED/g) ?? []).toHaveLength(0);
    expect(text).not.toContain("NO SUPPRESSION");
    expect(text.match(/signal suppressed/gi) ?? []).toHaveLength(1);
  });

  it("drops the header regime pill that repeated the verdict", () => {
    mockUseVcg.mockReturnValue(state());
    const { container } = render(<VcgPanel prices={{}} />);
    const pills = Array.from(container.querySelectorAll(".section-header .pill")).map((p) => p.textContent?.trim());
    expect(pills).not.toContain("DIVERGENCE");
  });
});
