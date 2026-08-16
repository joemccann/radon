/**
 * Defect E — two sizes of the same chip on screen at once.
 *
 * The item chips were shrunk to a 24px box with a pseudo-element hit area, but
 * `NewsfeedTagBar` was excluded, so its chips keep the global mobile
 * `button { min-height: 44px }` floor. With a filter active,
 * `.news-feed-tag-bar-chip SPX` renders 44px tall and `.news-feed-tag-chip SPX`
 * renders 24px tall, two rows apart, identical text, both teal-active.
 *
 * Resolution: one chip size. The tag-bar chip adopts the item chip's box and
 * the same `--touch-min` pseudo hit area, reached from inside the module's
 * card scope so nothing lands in globals.css (owned by another workflow).
 *
 * jsdom performs no layout, so the RENDERED heights are compared in
 * web/e2e/mobile-newsfeed-layout.spec.ts. What this file pins is the source
 * contract that makes those heights equal: both chip variants must declare the
 * same box and the same hit area, from the same stylesheet.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const moduleCss = readFileSync(
  join(__dirname, "..", "components", "DashboardNewsFeed.module.css"),
  "utf8",
);

const MOBILE = 'body[data-mobile="true"]';
const ITEM_CHIP = `:global(${MOBILE}) .tagChip.tagChip`;
// The tag bar renders inside `section.dashboard-news`, which carries the
// module's `card` class, so the module can reach its chips without touching
// globals.css or NewsfeedTagBar.tsx.
const BAR_CHIP = `:global(${MOBILE}) .card.card :global(.news-feed-tag-bar-chip)`;

function ruleBlock(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = moduleCss.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, "m"));
  expect(match, `rule not found for ${selector}`).not.toBeNull();
  return match![1];
}

function declaration(block: string, property: string): string | null {
  const match = block.match(new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;]+)`, "m"));
  return match ? match[1].trim() : null;
}

describe("newsfeed chip size parity — item chips and tag-bar chips are one size", () => {
  it("defines the tag-bar chip box inside the module, scoped to the mobile card", () => {
    const bar = ruleBlock(BAR_CHIP);
    expect(bar).toMatch(/min-height:\s*24px/);
    expect(bar).toMatch(/min-width:\s*auto/);
    expect(bar).toMatch(/position:\s*relative/);
  });

  it("declares the identical box on both chip variants", () => {
    const item = ruleBlock(ITEM_CHIP);
    const bar = ruleBlock(BAR_CHIP);
    for (const property of ["min-height", "min-width"]) {
      const itemValue = declaration(item, property);
      const barValue = declaration(bar, property);
      expect(itemValue, `item chip is missing ${property}`).not.toBeNull();
      expect(barValue, `tag-bar chip is missing ${property}`).toBe(itemValue);
    }
  });

  it("gives the tag-bar chip a 44px effective hit area, same as every other shrunk control", () => {
    const hit = ruleBlock(`${BAR_CHIP}::after`);
    expect(hit).toMatch(/content:\s*""/);
    expect(hit).toMatch(/position:\s*absolute/);
    expect(hit).toMatch(/var\(--touch-min\)/);
  });

  it("keeps the tag-bar chip rule scoped to the mobile shell so desktop cannot regress", () => {
    const selectors = moduleCss
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split("}")
      .map((chunk) => chunk.split("{")[0].trim())
      .filter((sel) => sel.length > 0 && !sel.startsWith("@"));
    const barChipSelectors = selectors.filter((sel) => sel.includes("news-feed-tag-bar-chip"));
    expect(barChipSelectors.length).toBeGreaterThan(0);
    barChipSelectors.forEach((sel) => expect(sel).toContain(MOBILE));
  });
});
