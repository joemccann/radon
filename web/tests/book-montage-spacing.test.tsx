// @vitest-environment jsdom

/**
 * Stock/option two-sided montage: SHARES and MARKET must not collide.
 *
 * AAOI repro (2026-09-04): ask inside was "195DRCTED..." because desktop
 * columns were `70px 1fr 52px` with no gap and ask size right-aligned into
 * a 52px venue cell. Combo/mobile already used column-gap; desktop stock
 * did not.
 *
 * T-458: this contract used to pin `column-gap: 8px` literals out of the
 * stylesheet bytes, so a later higher-specificity selector could reproduce
 * the collision while the pins stayed green. It now renders DepthMontage and
 * resolves the WINNING declaration for each rendered element through the
 * cascade helper (specificity + source order at the desktop viewport).
 */

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import { DepthMontage } from "../components/ticker-detail/DepthMontage";
import type { DepthBook, DepthLevel } from "../lib/pricesProtocol";
import { GLOBALS_CSS_RULES, resolvedColumnGap, winningDeclaration } from "./cssCascade";

afterEach(cleanup);

function level(price: number, size: number, marketMaker: string): DepthLevel {
  return { price, size, marketMaker, exchange: null };
}

const AAOI_BOOK: DepthBook = {
  symbol: "AAOI",
  kind: "stock",
  bid: [level(18.9, 200, "NSDQ"), level(18.85, 400, "OVERNIGHT")],
  ask: [level(19.0, 195, "DRCTEDARK"), level(19.05, 300, "NSDQ")],
  isSmartDepth: true,
  feed: "SMART DEPTH",
  entitled: true,
  timestamp: "2026-09-04T15:00:00Z",
};

function renderMontage() {
  const { container } = render(<DepthMontage book={AAOI_BOOK} />);
  const bidRow = container.querySelector(".book-side.bid .book-row");
  const askRow = container.querySelector(".book-side.ask .book-row");
  const bidColhead = container.querySelector(".book-side.bid .book-colhead");
  const askColhead = container.querySelector(".book-side.ask .book-colhead");
  expect(bidRow).toBeTruthy();
  expect(askRow).toBeTruthy();
  expect(bidColhead).toBeTruthy();
  expect(askColhead).toBeTruthy();
  return {
    container,
    bidRow: bidRow as Element,
    askRow: askRow as Element,
    bidColhead: bidColhead as Element,
    askColhead: askColhead as Element,
  };
}

describe("desktop stock/option montage spacing", () => {
  it("puts a column gap between market, size, and price on both sides", () => {
    const { bidRow, askRow, bidColhead, askColhead } = renderMontage();
    for (const element of [bidRow, askRow, bidColhead, askColhead]) {
      expect(resolvedColumnGap(element, GLOBALS_CSS_RULES)).toBe("8px");
    }
  });

  it("lets the market column shrink (minmax 0) so long MPIDs ellipsis instead of overflowing into size", () => {
    const { bidRow, askRow } = renderMontage();
    const bidColumns = winningDeclaration(bidRow, ["grid-template-columns"], GLOBALS_CSS_RULES);
    const askColumns = winningDeclaration(askRow, ["grid-template-columns"], GLOBALS_CSS_RULES);
    expect(bidColumns?.value).toMatch(/minmax\(0,/);
    expect(askColumns?.value).toMatch(/minmax\(0,/);
    expect(bidColumns?.value).not.toBe("52px 1fr 70px");
    expect(askColumns?.value).not.toBe("70px 1fr 52px");
  });

  it("left-aligns ask size so shares sit next to the ask price, not the venue", () => {
    const { askRow } = renderMontage();
    // The regression was ordering AND alignment: shares must sit between the
    // price and the venue in the rendered row, and the cascade must hand that
    // rendered cell text-align:left (the base `.book-row .book-shares` rule
    // says right — the ask-side override has to win).
    const cellClasses = Array.from(askRow.children, (cell) => cell.className);
    expect(cellClasses[0]).toContain("book-px");
    expect(cellClasses[1]).toContain("book-shares");
    expect(cellClasses[2]).toContain("book-mkt");
    const shares = askRow.querySelector(".book-shares") as Element;
    expect(winningDeclaration(shares, ["text-align"], GLOBALS_CSS_RULES)?.value).toBe("left");
  });

  it("clips venue labels with ellipsis instead of letting them paint into the size column", () => {
    const { askRow } = renderMontage();
    const venue = askRow.querySelector(".book-mkt") as Element;
    const venueLead = venue.querySelector(".book-venue-lead") as Element;
    expect(venue).toBeTruthy();
    expect(venueLead).toBeTruthy();
    expect(venueLead.textContent).toBe("DRCTEDARK");
    expect(winningDeclaration(venue, ["overflow"], GLOBALS_CSS_RULES)?.value).toBe("hidden");
    expect(winningDeclaration(venueLead, ["min-width"], GLOBALS_CSS_RULES)?.value).toBe("0");
    expect(winningDeclaration(venueLead, ["text-overflow"], GLOBALS_CSS_RULES)?.value).toBe(
      "ellipsis",
    );
  });

  it("clips colhead labels so MARKET and SHARES cannot concatenate", () => {
    const { askColhead } = renderMontage();
    const labels = Array.from(askColhead.querySelectorAll(":scope > span"));
    expect(labels.map((span) => span.textContent)).toEqual(["Ask", "Shares", "Market"]);
    for (const span of labels) {
      expect(winningDeclaration(span, ["overflow"], GLOBALS_CSS_RULES)?.value).toBe("hidden");
      expect(winningDeclaration(span, ["text-overflow"], GLOBALS_CSS_RULES)?.value).toBe(
        "ellipsis",
      );
    }
  });
});
