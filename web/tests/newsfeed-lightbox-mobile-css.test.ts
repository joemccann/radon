import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
const css = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8");
describe("article reader touch controls", () => {
 it("does not require an undiscoverable swipe to navigate articles", () => {
  expect(css).not.toMatch(/\.newsfeed-lightbox__control\s*\{[^}]*display:\s*none/);
  expect(css).toMatch(/\.newsfeed-lightbox__control\s*\{[^}]*min-width:\s*44px/);
  expect(css).toMatch(/\.newsfeed-lightbox__control\s*\{[^}]*min-height:\s*44px/);
 });
});
