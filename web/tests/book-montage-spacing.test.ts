/**
 * Stock/option two-sided montage: SHARES and MARKET must not collide.
 *
 * AAOI repro (2026-09-04): ask inside was "195DRCTED..." because desktop
 * columns were `70px 1fr 52px` with no gap and ask size right-aligned into
 * a 52px venue cell. Combo/mobile already used column-gap; desktop stock
 * did not.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../app/globals.css"), "utf8");

function stockMontageBlock(): string {
  const start = css.indexOf("/* two-sided montage (stock / option)");
  const end = css.indexOf("/* option per-exchange BBO montage");
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return css.slice(start, end);
}

function ruleBody(block: string, selector: string): string {
  const idx = block.indexOf(selector);
  expect(idx, `missing ${selector}`).toBeGreaterThan(-1);
  const open = block.indexOf("{", idx);
  const close = block.indexOf("}", open);
  expect(open).toBeGreaterThan(-1);
  expect(close).toBeGreaterThan(open);
  return block.slice(open, close + 1);
}

describe("desktop stock/option montage spacing", () => {
  it("puts a column gap between market, size, and price on both sides", () => {
    const block = stockMontageBlock();
    const bid = ruleBody(block, ".book-side.bid .book-colhead, .book-side.bid .book-row");
    const ask = ruleBody(block, ".book-side.ask .book-colhead, .book-side.ask .book-row");
    expect(bid).toMatch(/column-gap:\s*8px/);
    expect(ask).toMatch(/column-gap:\s*8px/);
  });

  it("lets the market column shrink (minmax 0) so long MPIDs ellipsis instead of overflowing into size", () => {
    const block = stockMontageBlock();
    const bid = ruleBody(block, ".book-side.bid .book-colhead, .book-side.bid .book-row");
    const ask = ruleBody(block, ".book-side.ask .book-colhead, .book-side.ask .book-row");
    expect(bid).toMatch(/minmax\(0,/);
    expect(ask).toMatch(/minmax\(0,/);
    expect(bid).not.toMatch(/52px 1fr 70px/);
    expect(ask).not.toMatch(/70px 1fr 52px/);
  });

  it("left-aligns ask size so shares sit next to the ask price, not the venue", () => {
    const block = stockMontageBlock();
    expect(block).toMatch(/\.book-side\.ask \.book-row \.book-shares[^}]*text-align:\s*left/);
  });

  it("clips venue labels with ellipsis instead of letting them paint into the size column", () => {
    expect(css).toMatch(/\.book-row \.book-mkt[^}]*overflow:\s*hidden/);
    expect(css).toMatch(/\.book-venue-lead[^}]*min-width:\s*0/);
  });

  it("clips colhead labels so MARKET and SHARES cannot concatenate", () => {
    expect(css).toMatch(/\.book-colhead > span[^}]*overflow:\s*hidden/);
    expect(css).toMatch(/\.book-colhead > span[^}]*text-overflow:\s*ellipsis/);
  });
});
