/**
 * Desktop act-ticket scroll contract (2026-08-14): Close Position + Gate 3 CRB
 * + confirm summary is taller than the act column. `.act-ticket` was
 * `flex: 0 0 auto` inside `.act-region { overflow: hidden }`, so Place /
 * Confirm clipped with no scrollport. The ticket must shrink and scroll.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../app/globals.css"), "utf8");

function ruleBlock(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start, `${selector} rule missing`).toBeGreaterThan(-1);
  const end = css.indexOf("}", start);
  return css.slice(start, end);
}

describe("desktop act-ticket scroll contract", () => {
  it(".act-ticket shrinks and scrolls so Place / Confirm stay reachable", () => {
    const block = ruleBlock(".act-ticket");
    expect(block).toMatch(/flex:\s*0 1 auto/);
    expect(block).toMatch(/min-height:\s*0/);
    expect(block).toMatch(/overflow-y:\s*auto/);
    expect(block).not.toMatch(/flex:\s*0 0 auto/);
  });

  it(".act-region still clips as a column so the ticket is the scrollport", () => {
    const block = ruleBlock(".act-region");
    expect(block).toMatch(/overflow:\s*hidden/);
    expect(block).toMatch(/min-height:\s*0/);
  });
});
