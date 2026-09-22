import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Layout contract for the daily dark-pool history toggle + buy-% chart.
 * The toggle is a header control (40px hit area, no transition:all).
 * The chart title is a module header rail, not a floating label.
 * The session table uses the shared `.table-wrap` + a sticky DATE column
 * so PRINTS cannot clip at the card edge on a phone.
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

  it("keeps the session table inside the shared overflow-x wrapper", () => {
    expect(ruleBody(".table-wrap")).toMatch(/overflow-x:\s*auto/);
    const src = readFileSync(
      join(__dirname, "..", "components/flow-analysis/DailyDarkPoolHistory.tsx"),
      "utf8",
    );
    expect(src).toMatch(/className="table-wrap"/);
    expect(src).toMatch(/data-testid="daily-dp-history-table-wrap"/);
  });

  it("sticks the DATE column and refuses to wrap cells on a phone", () => {
    const mobile = css.slice(css.indexOf("@media (max-width: 640px)"));
    const dailyMobile = mobile.slice(mobile.lastIndexOf(".ticker-flow-daily {"));
    expect(dailyMobile).toMatch(/min-width:\s*max-content/);
    expect(mobile).toMatch(
      /\.ticker-flow-daily th,\s*\.ticker-flow-daily td\s*\{[^}]*white-space:\s*nowrap/,
    );
    expect(mobile).toMatch(
      /\.ticker-flow-daily th:first-child,\s*\.ticker-flow-daily td:first-child\s*\{[^}]*position:\s*sticky/,
    );
    expect(mobile).toMatch(
      /\.ticker-flow-daily th:first-child,\s*\.ticker-flow-daily td:first-child\s*\{[^}]*left:\s*0/,
    );
  });
});
