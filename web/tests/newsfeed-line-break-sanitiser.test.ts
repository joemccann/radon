/**
 * Market Ear bodies arrive with hard newlines that mean two different things,
 * and `white-space: pre-wrap` rendered both of them literally: mid-sentence
 * source wraps broke a running sentence into two ragged lines, and block
 * joins between the article's own paragraphs rendered as tight single breaks.
 *
 * Census, Turso `posts` 2026-08-25: 5,277 bodies, 1,038 with a newline,
 * 1,932 newlines total, ZERO blank-line paragraph breaks anywhere.
 *   next line starts lowercase                  ×42    soft wrap  -> join
 *   previous line ends on a function word       ×7     soft wrap  -> join
 *   previous line ends '.?!…' + next opens      ×1,599 block join -> paragraph
 *   everything else (lists, data rows, %, :)    ×284   -> keep the hard break
 *
 * Every one of the 49 join cases was read by hand off production before the
 * rule was written; none is a list item.
 */

import { describe, expect, it } from "vitest";

import { normalisePostContent } from "../lib/newsfeedText";

/** Turso post "The twist" (2026-08-20), truncated. Two mid-sentence wraps. */
const TWIST = [
  "The DM examples tell us that term premia can be compressed. The EM examples tell",
  "us that once markets focus on sovereign financing dynamics, yield suppression becomes",
  "progressively less effective. The most likely outcome is therefore a temporary flattening, not a",
  "permanent solution.",
].join("\n");

/** Turso post "Saravelos says" (2026-08-20), truncated. Pure block joins. */
const SARAVELOS = [
  "Deutsche Bank’s George Saravelos argues that Treasury’s buyback is effectively soft financial repression.",
  "His logic is simple: if Treasury prices aren’t “allowed” to adjust lower, the adjustment has to happen somewhere else.",
  "Saravelos draws parallels with Operation Twist.",
].join("\n");

describe("normalisePostContent — scrape line-break sanitisation", () => {
  it("joins a mid-sentence wrap whose next line starts lowercase", () => {
    expect(normalisePostContent(TWIST)).toBe(
      "The DM examples tell us that term premia can be compressed. The EM examples tell us that once markets focus on sovereign financing dynamics, yield suppression becomes progressively less effective. The most likely outcome is therefore a temporary flattening, not a permanent solution.",
    );
  });

  it("joins a wrap whose previous line ends on a function word", () => {
    expect(normalisePostContent("Rising private credit and\nEM-Sovereign spreads widened.")).toBe(
      "Rising private credit and EM-Sovereign spreads widened.",
    );
    expect(normalisePostContent("in the 95th percentile vs. the past year and in the\n99th percentile vs. the past 5 years")).toBe(
      "in the 95th percentile vs. the past year and in the 99th percentile vs. the past 5 years",
    );
  });

  it("promotes a sentence-boundary break into a paragraph break", () => {
    expect(normalisePostContent(SARAVELOS)).toBe(
      [
        "Deutsche Bank’s George Saravelos argues that Treasury’s buyback is effectively soft financial repression.",
        "His logic is simple: if Treasury prices aren’t “allowed” to adjust lower, the adjustment has to happen somewhere else.",
        "Saravelos draws parallels with Operation Twist.",
      ].join("\n\n"),
    );
  });

  it("promotes a break before an opening quote glyph", () => {
    expect(normalisePostContent("Hartnett is out.\n“The value of financial assets is 6.5x GDP.”")).toBe(
      "Hartnett is out.\n\n“The value of financial assets is 6.5x GDP.”",
    );
  });

  it("keeps data rows and list items on their own tight lines", () => {
    const rows = "Forward P/E ratios...\nCostco: 45\nWalmart: 36\nNvidia: 17";
    expect(normalisePostContent(rows)).toBe(rows);
    const numbered = "1. The full impact of the Iran war is still to come\n2. Powell stays hawkish\n3. Warsh will be tested early";
    expect(normalisePostContent(numbered)).toBe(numbered);
    const bullets = "— 38 at OpenAI\n— 15 at Anthropic";
    expect(normalisePostContent(bullets)).toBe(bullets);
    const percents = "GOOGL\n+467%\nMSFT\n+367%";
    expect(normalisePostContent(percents)).toBe(percents);
  });

  it("keeps a break after a line that ends without terminal punctuation", () => {
    const body = "US CEOs are a decade older than in 2000\nThe same trend is true amongst our politicians";
    expect(normalisePostContent(body)).toBe(body);
  });

  it("leaves an existing paragraph break alone and is idempotent", () => {
    for (const input of [TWIST, SARAVELOS, "a\n\nb", "GOOGL\n+467%"]) {
      const once = normalisePostContent(input);
      expect(normalisePostContent(once), input).toBe(once);
    }
  });

  it("trims trailing spaces left on a wrapped line", () => {
    expect(normalisePostContent("The wealth effect from stocks for US   \n   households has become massive.")).toBe(
      "The wealth effect from stocks for US households has become massive.",
    );
  });

  it("still strips an unpartnered leading double quote after rewrapping", () => {
    expect(normalisePostContent('"All three months of the second quarter ranked\namong the strongest periods.')).toBe(
      "All three months of the second quarter ranked among the strongest periods.",
    );
  });
});

describe("normalisePostContent — degenerate whitespace", () => {
  it("drops whitespace-only blocks instead of stacking blank lines", () => {
    expect(normalisePostContent("Rates are up.\n\n   \n\nThe curve steepened.")).toBe(
      "Rates are up.\n\nThe curve steepened.",
    );
  });

  it("returns an empty string for whitespace-only content", () => {
    expect(normalisePostContent("   \n\n  ")).toBe("");
    expect(normalisePostContent("")).toBe("");
  });
});
