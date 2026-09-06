/**
 * @vitest-environment jsdom
 *
 * Contract for the brand-true Laplace curvature rendering that replaces the
 * generic horizontal bar chart in the GEX panel. The Laplace engine in the
 * brand kit speaks in curvature surfaces and field lines (docs/brand-identity.md
 * § 7), and gamma exposure is fundamentally a second derivative of price w.r.t.
 * spot — so the chart geometry should read as curvature, not as discrete bars.
 *
 * These tests pin the structural pieces an operator visually decodes:
 *   - filled curvature areas (positive / negative net GEX) using brand tokens
 *   - a vertical flip line where net GEX crosses zero
 *   - a spot price marker
 *   - level markers (max magnet, accelerator, walls) at their strikes
 *   - a single readout strip above the SVG (no Recharts tooltip)
 */

import React from "react";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import GexLaplaceContour from "../components/instruments/GexLaplaceContour";
import type { GexBucket } from "../lib/useGex";

const PROFILE: GexBucket[] = [
  { strike: 5400, call_gex: 100,  put_gex: -800,  net_gex: -700, pct_from_spot: -3.27, tag: null },
  { strike: 5450, call_gex: 200,  put_gex: -900,  net_gex: -700, pct_from_spot: -2.4,  tag: null },
  { strike: 5500, call_gex: 400,  put_gex: -1900, net_gex: -1500, pct_from_spot: -1.48, tag: "MAX ACCELERATOR" },
  { strike: 5537, call_gex: 600,  put_gex: -650,  net_gex: -50,  pct_from_spot: -0.82, tag: "GEX FLIP" },
  { strike: 5575, call_gex: 900,  put_gex: -300,  net_gex: 600,  pct_from_spot: -0.14, tag: "SPOT" },
  { strike: 5650, call_gex: 1400, put_gex: -200,  net_gex: 1200, pct_from_spot: 1.21,  tag: null },
  { strike: 5700, call_gex: 2200, put_gex: -150,  net_gex: 2050, pct_from_spot: 2.1,   tag: "MAX MAGNET" },
];

function renderContour(overrides: Partial<React.ComponentProps<typeof GexLaplaceContour>> = {}) {
  const props: React.ComponentProps<typeof GexLaplaceContour> = {
    profile: PROFILE,
    spotPrice: 5582.69,
    flipStrike: 5537,
    maxMagnet: 5700,
    maxAccelerator: 5500,
    putWall: 5450,
    callWall: 5700,
    ticker: "SPX",
    ...overrides,
  };
  return render(<GexLaplaceContour {...props} />);
}

function parseXAttr(el: Element | null): number {
  return Number(el?.getAttribute("x1") ?? el?.getAttribute("x") ?? "NaN");
}

describe("GexLaplaceContour", () => {
  it("renders a single SVG (no Recharts wrapper)", () => {
    const { container } = renderContour();
    expect(container.querySelectorAll("svg")).toHaveLength(1);
  });

  it("draws a positive curvature area filled with the signal-core token", () => {
    const { container } = renderContour();
    const positiveArea = container.querySelector('[data-testid="gex-curvature-positive"]');
    expect(positiveArea).toBeTruthy();
    const fill = positiveArea?.getAttribute("fill") ?? "";
    expect(fill).toContain("color-mix");
    expect(fill).toContain("var(--signal-core)");
    expect(fill).not.toMatch(/#[0-9a-fA-F]{6}/);
  });

  it("draws a negative curvature area filled with the dislocation token", () => {
    const { container } = renderContour();
    const negativeArea = container.querySelector('[data-testid="gex-curvature-negative"]');
    expect(negativeArea).toBeTruthy();
    const fill = negativeArea?.getAttribute("fill") ?? "";
    expect(fill).toContain("color-mix");
    expect(fill).toContain("var(--dislocation)");
    expect(fill).not.toMatch(/#[0-9a-fA-F]{6}/);
  });

  it("traces the net GEX contour as a 2px stroke in signal-core", () => {
    const { container } = renderContour();
    const trace = container.querySelector('[data-testid="gex-curvature-trace"]');
    expect(trace).toBeTruthy();
    expect(trace?.getAttribute("stroke")).toBe("var(--signal-core)");
    expect(trace?.getAttribute("stroke-width")).toBe("2");
  });

  it("positions the flip line at the strike where net GEX crosses zero", () => {
    const { container } = renderContour();
    const flipLine = container.querySelector('[data-testid="gex-flip-line"]');
    expect(flipLine).toBeTruthy();
    expect(flipLine?.getAttribute("stroke")).toBe("var(--warn)");
    expect(flipLine?.getAttribute("stroke-dasharray")).toBeTruthy();

    const spotLine = container.querySelector('[data-testid="gex-spot-line"]');
    const flipX = parseXAttr(flipLine);
    const spotX = parseXAttr(spotLine);

    // Spot 5582.69 is above flip 5537 in the profile, so the spot marker
    // should sit to the right of the flip line on a strike-ordered axis.
    expect(Number.isFinite(flipX)).toBe(true);
    expect(Number.isFinite(spotX)).toBe(true);
    expect(spotX).toBeGreaterThan(flipX);
  });

  it("renders the spot label with the ticker and price", () => {
    const { container } = renderContour();
    expect(container.textContent).toContain("SPOT");
    expect(container.textContent).toContain("5,582.69");
  });

  it("renders level markers for max magnet, accelerator, and put wall", () => {
    const { container } = renderContour();
    expect(container.querySelector('[data-testid="gex-level-marker-max-magnet"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="gex-level-marker-max-accelerator"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="gex-level-marker-put-wall"]')).toBeTruthy();
  });

  it("keeps financial marker labels at Clear's 12px minimum", () => {
    const { container } = renderContour();
    const labels = container.querySelectorAll('[data-testid^="gex-level-marker-"] text');
    expect(labels.length).toBeGreaterThan(0);
    labels.forEach((label) => expect(Number(label.getAttribute("font-size"))).toBeGreaterThanOrEqual(12));
  });

  it("renders projection-geometry guides as decorative diagonals", () => {
    const { container } = renderContour();
    const guides = container.querySelectorAll('[data-testid="gex-projection-guide"]');
    expect(guides.length).toBeGreaterThanOrEqual(3);
    guides.forEach((g) => {
      expect(g.getAttribute("stroke")).toBe("var(--line-grid)");
    });
  });

  it("renders a readout strip with the default-anchored strike, net GEX, curvature", () => {
    const { container } = renderContour();
    const readout = container.querySelector('[data-testid="gex-readout"]');
    expect(readout).toBeTruthy();
    const text = readout?.textContent ?? "";
    expect(text).toContain("STRIKE");
    expect(text).toContain("NET GEX");
    expect(text).toContain("CURVATURE");
  });

  it("does not embed any raw hex color literals", () => {
    const { container } = renderContour();
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    const markup = svg?.outerHTML ?? "";
    expect(markup).not.toMatch(/#[0-9a-fA-F]{6}\b/);
    expect(markup).not.toMatch(/#[0-9a-fA-F]{3}\b/);
    expect(markup).not.toMatch(/rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+/);
  });
});
