/**
 * Strike identity on the options chain must stay fully readable.
 *
 * Production (SNDK 2026-09-17): chain-first pinned the strike col at 72px and
 * inherited `text-overflow: ellipsis` from `.chain-cell`, so `$1,737.00`
 * rendered as `$1,70...`.
 *
 * fmtPrice always emits `$` + grouping + two decimals (`$1,737.00`,
 * `$12,345.00`). Size the col for that string at the chain-first 13px tabular
 * face with 6px inline padding.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { fmtPrice } from "../lib/positionUtils";

const GLOBALS = readFileSync(join(__dirname, "../app/globals.css"), "utf8");
const CHAIN_FIRST = readFileSync(
  join(__dirname, "../components/ticker-detail/ChainFirst.module.css"),
  "utf8",
);

/** Longest common listed-strike label: 5 integer digits with grouping. */
const FIVE_DIGIT_STRIKE = fmtPrice(12_345);
const MIN_STRIKE_COL_PX = 120;

function ruleAfter(css: string, marker: string): string {
  const at = css.indexOf(marker);
  expect(at, `missing CSS marker ${marker}`).toBeGreaterThan(-1);
  return css.slice(at, css.indexOf("}", at));
}

function widthPx(block: string): number {
  const match = block.match(/(?:^|[{\s;])width:\s*([\d.]+)(px|rem)/);
  expect(match, `width in ${block}`).not.toBeNull();
  const value = Number(match![1]);
  return match![2] === "rem" ? value * 16 : value;
}

describe("chain strike column", () => {
  it("formats the SNDK-class strike the operator could not read", () => {
    expect(fmtPrice(1737)).toBe("$1,737.00");
    expect(FIVE_DIGIT_STRIKE).toBe("$12,345.00");
    expect(FIVE_DIGIT_STRIKE.length).toBe(10);
  });

  it("keeps the strike col wide enough for five-digit grouped labels", () => {
    const globalCol = ruleAfter(GLOBALS, ".chain-anchor-strike-col {");
    const firstCol = ruleAfter(CHAIN_FIRST, ".chain-anchor-strike-col) {");
    expect(widthPx(globalCol), globalCol).toBeGreaterThanOrEqual(MIN_STRIKE_COL_PX);
    expect(widthPx(firstCol), firstCol).toBeGreaterThanOrEqual(MIN_STRIKE_COL_PX);
  });

  it("does not ellipsis the strike identity", () => {
    const strike = ruleAfter(GLOBALS, ".chain-anchor-panes td.chain-strike,");
    expect(strike).toMatch(/text-overflow:\s*clip/);
    expect(strike).not.toMatch(/text-overflow:\s*ellipsis/);
    const cellAt = GLOBALS.indexOf(".chain-anchor-panes .chain-cell {");
    const clipAt = GLOBALS.indexOf(".chain-anchor-panes td.chain-strike,");
    expect(cellAt).toBeGreaterThan(-1);
    expect(clipAt).toBeGreaterThan(cellAt);
    expect(CHAIN_FIRST).not.toMatch(/chain-strike[^{]*\{[^}]*text-overflow:\s*ellipsis/s);
  });
});
