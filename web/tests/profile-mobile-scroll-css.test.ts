/**
 * Mobile Profile Prefs/Keys render `.preferences-shell` as a direct child of
 * `.profile-surface--mobile`. Only `.profile-list` / `.profile-empty` used to
 * scroll, so those panels sat under the tab bar. The four-tab `.m-segment`
 * also shrink-clipped BOOKMARKS on ~390px. CSS-only contract.
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
  it("gives .preferences-shell the same flex/overflow as .profile-list", () => {
    expect(css).toMatch(
      /\.profile-surface--mobile\s*>\s*\.profile-list[\s\S]*?\.profile-surface--mobile\s*>\s*\.preferences-shell/,
    );
    const shared = css.slice(
      css.indexOf(".profile-surface--mobile > .profile-list"),
      css.indexOf(".profile-surface--mobile > .m-segment {"),
    );
    expect(shared).toMatch(/flex:\s*1 1 auto/);
    expect(shared).toMatch(/min-height:\s*0/);
    expect(shared).toMatch(/overflow-y:\s*auto/);
    expect(shared).not.toMatch(/profile-panel-scroll/);
  });

  it("lets the profile segment scroll horizontally so BOOKMARKS does not clip", () => {
    const block = ruleBlock(".profile-surface--mobile > .m-segment");
    expect(block).toMatch(/overflow-x:\s*auto/);
    expect(block).toMatch(/scroll-snap-type:\s*x proximity/);
    expect(block).toMatch(/scrollbar-width:\s*none/);

    const item = ruleBlock(".profile-surface--mobile > .m-segment > .m-segment__item");
    expect(item).toMatch(/flex:\s*0 0 auto/);
    expect(item).toMatch(/white-space:\s*nowrap/);
    expect(item).not.toMatch(/min-width:\s*0/);
  });
});
