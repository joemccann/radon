import { describe, expect, it } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";
import { REGIME_QUADRANT_DETAILS } from "../lib/regimeRelationships";
import { SECTION_TOOLTIPS } from "../lib/sectionTooltips";

const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));
const VIEW_PATH = join(TEST_DIR, "../components/RegimeRelationshipView.tsx");
const source = readFileSync(VIEW_PATH, "utf-8");

describe("Regime relationship state tooltips", () => {
  it("exports definitions for all four quadrant states", () => {
    expect(Object.keys(REGIME_QUADRANT_DETAILS)).toEqual([
      "Systemic Panic",
      "Fragile Calm",
      "Stock Picker's Market",
      "Goldilocks",
    ]);
    expect(REGIME_QUADRANT_DETAILS["Fragile Calm"]).toContain("RVOL is below its 20-session mean");
    expect(REGIME_QUADRANT_DETAILS["Systemic Panic"]).toContain("COR1M is at or above its 20-session mean");
  });

  it("renders a state key with tooltip triggers for the four relationship states", () => {
    expect(source).toContain('data-testid="regime-state-key"');
    expect(source).toContain("regime-state-tooltip-trigger");
    expect(source).toContain("regime-state-tooltip-bubble");
    expect(source).toContain("STATE KEY");
  });

  it("renders a tooltip trigger for the normalized divergence panel", () => {
    expect(SECTION_TOOLTIPS["NORMALIZED DIVERGENCE"]).toContain("z-score");
    expect(source).toContain("regime-zscore-tooltip-trigger");
    expect(source).toContain("regime-zscore-tooltip-bubble");
    expect(source).toContain("NORMALIZED DIVERGENCE");
  });

  it("makes the normalized divergence tooltip actionable for portfolio posture", () => {
    expect(SECTION_TOOLTIPS["NORMALIZED DIVERGENCE"]).toContain("reduce gross exposure");
    expect(SECTION_TOOLTIPS["NORMALIZED DIVERGENCE"]).toContain("keep or add index hedges");
    expect(SECTION_TOOLTIPS["NORMALIZED DIVERGENCE"]).toContain("harvest crash hedges");
    expect(SECTION_TOOLTIPS["NORMALIZED DIVERGENCE"]).toContain("single-name");
  });

  it("renders hover telemetry hooks for the normalized divergence chart", () => {
    expect(source).toContain('data-testid="regime-zscore-chart-overlay"');
    expect(source).toContain('data-testid="regime-zscore-hover-tooltip"');
    expect(source).toContain('data-testid="regime-zscore-hover-date"');
  });

  it("colors the latest quadrant marker from the classified quadrant instead of a hardcoded warning tone", () => {
    expect(source).toContain('const latestQuadrantColor = quadrantTone(latest.quadrant);');
    expect(source).toContain('fill={isLatest ? latestQuadrantColor : "var(--signal-core)"}');
    expect(source).toContain('stroke={isLatest ? latestQuadrantColor : "none"}');
  });
});
