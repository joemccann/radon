/**
 * P2 (tasks/mobile-parity-audit.md): the L2 book montage used 9px column
 * headers and 12px rows, illegible on a phone. Pins the .cockpit--mobile
 * legibility overrides in globals.css (montage needs live IB depth, so this
 * is a source contract rather than a rendered assertion).
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../app/globals.css"), "utf8");

function mobileBookBlock(): string {
  const start = css.indexOf("/* Book legibility on phones");
  expect(start).toBeGreaterThan(-1);
  return css.slice(start, start + 700);
}

describe("mobile book montage legibility", () => {
  it("bumps colhead type above 9px under .cockpit--mobile", () => {
    const block = mobileBookBlock();
    expect(block).toContain(".cockpit--mobile .book-colhead");
    expect(block).toMatch(/\.cockpit--mobile \.book-colhead[^}]*font-size: 1[0-9]px/);
  });

  it("bumps row and note type under .cockpit--mobile", () => {
    const block = mobileBookBlock();
    expect(block).toMatch(/\.cockpit--mobile \.book-row[^}]*font-size: 13px/);
    expect(block).toMatch(/\.cockpit--mobile \.book-row \.book-mkt[^}]*font-size: 12px/);
    expect(block).toMatch(/\.cockpit--mobile \.book-montage-note[^}]*font-size: 11px/);
  });
});
