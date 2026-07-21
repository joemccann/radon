/**
 * iPhone order-entry cutoff regression (2026-07-21): the bottom of the mobile
 * order ticket (sticky footer with Review / Confirm & send) was unviewable and
 * unscrollable on iOS. Two source contracts pin the fix:
 *
 *  1. `.m-sheet` must cap its height with dvh (dynamic viewport) units clamped
 *     by the live keyboard inset — never bare `vh`, which iOS resolves against
 *     the LARGE viewport (Safari toolbar collapsed) and so overflows the
 *     visible area whenever the toolbar is showing.
 *  2. `.mobile-sheet-root` must pad its bottom by `--keyboard-inset` so the
 *     bottom-anchored sheet lifts above the iOS keyboard (the decimal pad has
 *     no Done key and covers the sticky footer otherwise).
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
  it(".m-sheet clamps max-height by --sheet-max-h and the keyboard inset, in dvh", () => {
    const block = ruleBlock(".m-sheet");
    expect(block).toContain(
      "max-height: min(var(--sheet-max-h, 88dvh), calc(100dvh - var(--keyboard-inset, 0px)))",
    );
    expect(block).not.toMatch(/\d+vh/);
  });

  it(".mobile-sheet-root lifts the sheet above the keyboard", () => {
    const block = ruleBlock(".mobile-sheet-root");
    expect(block).toContain("padding-bottom: var(--keyboard-inset, 0px)");
  });

  it(".mobile-sheet legacy cap also avoids bare vh units", () => {
    const block = ruleBlock(".mobile-sheet");
    expect(block).not.toMatch(/max-height:\s*\d+vh\b/);
  });
});
