import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Bordered tile grids seated in a bordered `.section` must never sit flush
 * against the section frame — two adjacent 1px borders read as one doubled
 * 2px line (flow-analysis report, performance methodology, VCG signal).
 *
 * Two sanctioned treatments:
 *  - floating-card grids (`.ticker-flow-grid`, `.performance-meta-grid`)
 *    inset from the frame with padding;
 *  - fused `.metric-card` grids overlay the frame with -1px top/left margins
 *    so card borders and the section border collapse into a single line.
 */
const css = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8");

function ruleBody(selector: string): string {
  const start = css.indexOf(selector);
  expect(start, `selector not found: ${selector}`).toBeGreaterThan(-1);
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  return css.slice(open + 1, close);
}

describe("section tile grids do not double the panel border", () => {
  it("insets the flow-analysis metric grid from the section frame", () => {
    expect(ruleBody(".ticker-flow-grid {")).toMatch(/padding:\s*16px/);
  });

  it("insets the performance methodology grid from the section frame", () => {
    expect(ruleBody(".section-body.performance-meta-grid {")).toMatch(
      /padding:\s*14px 16px 16px/,
    );
  });

  it("fuses metric-card grids into the section frame", () => {
    expect(
      ruleBody(
        ".section > :is(.metrics-grid, .metrics-grid-3, .metrics-grid-5),",
      ),
    ).toMatch(/margin:\s*-1px 0 20px -1px/);
    expect(css).toContain(
      ".section-body > :is(.metrics-grid, .metrics-grid-3, .metrics-grid-5):last-child",
    );
  });
});
