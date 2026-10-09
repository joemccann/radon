/**
 * iPhone order-entry cutoff regression (2026-07-21): the bottom of the mobile
 * order ticket (sticky footer with Review / Confirm & send) was unviewable and
 * unscrollable on iOS. Two source contracts pin the fix:
 *
 *  1. `.m-sheet` must cap its height with dvh (dynamic viewport) units clamped
 *     by the live visual-viewport height — never bare `vh`, which iOS resolves
 *     against the LARGE viewport (Safari toolbar collapsed) and so overflows
 *     the visible area whenever the toolbar is showing.
 *  2. `.mobile-sheet-root` must be pinned to the visual viewport
 *     (`--vv-top` / `--vv-height`), not `inset: 0`. The keyboard and iOS
 *     standalone layout-viewport drift both leave the layout viewport taller
 *     than what is visible; a bottom-anchored sheet then hangs off-screen
 *     (order modify/cancel sheet, 2026-10-09).
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

describe("mobile sheet iOS viewport contract", () => {
  it(".m-sheet clamps max-height by --sheet-max-h and the visible height, in dvh", () => {
    const block = ruleBlock(".m-sheet");
    expect(block).toContain("max-height: min(var(--sheet-max-h, 88dvh), var(--vv-height, 100dvh))");
    expect(block).not.toMatch(/\d+vh/);
  });

  it(".mobile-sheet-root is pinned to the visual viewport", () => {
    const block = ruleBlock(".mobile-sheet-root");
    expect(block).toContain("top: var(--vv-top, 0px)");
    expect(block).toContain("height: var(--vv-height, 100dvh)");
    expect(block).not.toMatch(/^\s*inset:/m);
  });

  it(".mobile-sheet legacy cap also avoids bare vh units", () => {
    const block = ruleBlock(".mobile-sheet");
    expect(block).not.toMatch(/max-height:\s*\d+vh\b/);
  });
});
