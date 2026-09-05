import { describe, expect, it } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";
import { nearestRegimeScatterIndex } from "../components/RegimeRelationshipView";

const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));
const PANEL_PATH = join(TEST_DIR, "../components/RegimePanel.tsx");
const VIEW_PATH = join(TEST_DIR, "../components/RegimeRelationshipView.tsx");
const CSS_PATH = join(TEST_DIR, "../app/globals.css");
const panelSource = readFileSync(PANEL_PATH, "utf-8");
const viewSource = readFileSync(VIEW_PATH, "utf-8");
const cssSource = readFileSync(CSS_PATH, "utf-8");

function cssRule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = cssSource.match(new RegExp(`${escaped}\\s*\\{([\\s\\S]*?)\\n\\}`));
  if (!match) {
    throw new Error(`Missing CSS rule for ${selector}`);
  }
  return match[1];
}

describe("CRI chart layout — full-width stack", () => {
  it("stacks the analytical history charts in a single column", () => {
    expect(panelSource).toContain('className="regime-history-grid"');
    expect(panelSource).toContain('data-testid="regime-history-grid"');
    expect(cssRule(".regime-history-grid")).toMatch(/grid-template-columns:\s*1fr/);
    expect(cssRule(".regime-history-grid")).not.toMatch(/repeat\(2/);
  });

  it("keeps Correlation Risk Premium on its existing wide panel contract", () => {
    expect(viewSource).toContain('data-testid="regime-spread-card"');
    expect(viewSource).toContain("CORRELATION RISK PREMIUM");
    expect(viewSource).toContain("regime-relationship-panel-wide");
    expect(viewSource).toContain('data-testid="regime-spread-range-chips"');
    expect(viewSource).toContain('data-testid="regime-spread-brush"');
  });

  it("makes regime quadrants and normalized divergence full-width panels", () => {
    const quadrantOpen = viewSource.slice(
      Math.max(0, viewSource.indexOf('data-testid="regime-quadrant-card"') - 220),
      viewSource.indexOf('data-testid="regime-quadrant-card"') + 40,
    );
    const zscoreOpen = viewSource.slice(
      Math.max(0, viewSource.indexOf('data-testid="regime-zscore-card"') - 220),
      viewSource.indexOf('data-testid="regime-zscore-card"') + 40,
    );
    expect(quadrantOpen).toContain("regime-relationship-panel-wide");
    expect(zscoreOpen).toContain("regime-relationship-panel-wide");
    expect(cssRule(".regime-relationship-grid")).toMatch(/grid-template-columns:\s*1fr/);
  });
});

describe("CRI quadrant scatter hover", () => {
  it("exposes hover shell, overlay, and tooltip hooks on the quadrant scatter", () => {
    expect(viewSource).toContain('data-testid="regime-quadrant-chart-shell"');
    expect(viewSource).toContain('data-testid="regime-quadrant-chart-overlay"');
    expect(viewSource).toContain('data-testid="regime-quadrant-hover-tooltip"');
    expect(viewSource).toContain('data-testid="regime-quadrant-hover-date"');
    expect(viewSource).toContain("Quadrant");
    expect(viewSource).toContain("RVOL");
    expect(viewSource).toContain("COR1M");
  });

  it("picks the nearest scatter point in plot space", () => {
    const points = [
      { x: 10, y: 10 },
      { x: 80, y: 40 },
      { x: 120, y: 90 },
    ];
    expect(nearestRegimeScatterIndex(points, 12, 8)).toBe(0);
    expect(nearestRegimeScatterIndex(points, 78, 42)).toBe(1);
    expect(nearestRegimeScatterIndex(points, 200, 200)).toBe(2);
  });
});
