/**
 * Mobile Profile Prefs/Keys were not a `.profile-list` / `.profile-empty`
 * child, so they never received the flex + overflow-y treatment and sat
 * under the fixed tab bar. The four-tab `.m-segment` also shrink-clipped
 * BOOKMARKS on ~390px. These rules pin the smallest CSS contract.
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

describe("mobile profile Prefs/Keys scroll + tab strip", () => {
  it("gives .profile-panel-scroll the same flex/overflow as .profile-list", () => {
    expect(css).toMatch(
      /\.profile-surface--mobile\s*>\s*\.profile-list[\s\S]*?\.profile-surface--mobile\s*>\s*\.profile-panel-scroll/,
    );
    const shared = css.slice(
      css.indexOf(".profile-surface--mobile > .profile-list"),
      css.indexOf(".profile-surface--mobile .m-sticky-cta"),
    );
    expect(shared).toMatch(/flex:\s*1 1 auto/);
    expect(shared).toMatch(/min-height:\s*0/);
    expect(shared).toMatch(/overflow-y:\s*auto/);
  });

  it("pads the Prefs/Keys scroller so last fields clear the tab bar", () => {
    expect(css).toMatch(
      /\.profile-surface--mobile\s*>\s*\.profile-panel-scroll\s*\{[^}]*padding-bottom:\s*calc\(var\(--mobile-tab-bar-height\)\s*\+\s*var\(--safe-bottom\)\s*\+\s*16px\)/,
    );
  });

  it("lets the profile segment scroll horizontally so BOOKMARKS does not clip", () => {
    const block = ruleBlock(".profile-surface--mobile > .m-segment");
    expect(block).toMatch(/overflow-x:\s*auto/);
    expect(block).toMatch(/scrollbar-width:\s*none/);

    const item = ruleBlock(".profile-surface--mobile > .m-segment > .m-segment__item");
    expect(item).toMatch(/flex:\s*0 0 auto/);
    expect(item).toMatch(/white-space:\s*nowrap/);
    expect(item).not.toMatch(/min-width:\s*0/);
  });
});
