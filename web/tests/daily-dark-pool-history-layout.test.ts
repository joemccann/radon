import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Layout contract for the daily dark-pool history toggle + buy-% chart.
 * The toggle is a header control (40px hit area, no transition:all).
 * The chart title is a module header rail, not a floating label.
 */
const css = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8");

function ruleBody(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start, `selector not found: ${selector}`).toBeGreaterThan(-1);
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  return css.slice(open + 1, close);
}

describe("daily dark-pool history layout", () => {
  it("gives the session toggle a 40px desktop hit area and specific transitions", () => {
    const body = ruleBody(".ticker-flow-history-toggle");
    expect(body).toMatch(/transition-property:\s*color,\s*border-color,\s*scale/);
    expect(body).not.toMatch(/transition:\s*all/);
    expect(ruleBody(".ticker-flow-history-toggle::after")).toMatch(/height:\s*40px/);
    expect(ruleBody(".ticker-flow-history-toggle:active")).toMatch(/scale:\s*0\.96/);
  });

  it("lays the chart title out as a header rail with window meta", () => {
    const header = ruleBody(".ticker-flow-history-chart-header");
    expect(header).toMatch(/display:\s*flex/);
    expect(header).toMatch(/justify-content:\s*space-between/);
    expect(css).toContain(".ticker-flow-history-chart-meta");
  });
});
