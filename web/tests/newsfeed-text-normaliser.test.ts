/**
 * Defect C — the quote normaliser must never invert on a word-final apostrophe.
 *
 * The shipped implementation counted quote-glyph PARITY after excluding
 * intra-word apostrophes, so a plural possessive ("the traders' book") or a
 * decade elision ("'Tis") flipped the decision and made the balanced case
 * WORSE than no normalisation at all.
 *
 * The rule pinned here is deliberately conservative and has no parity count:
 *
 *   Strip a leading quote glyph ONLY when BOTH hold:
 *     1. it is a DOUBLE-quote-class character — U+0022 " , U+201C “ , U+201D ” ;
 *     2. no other double-quote-class character appears anywhere in the rest.
 *   Single-quote and apostrophe glyphs (U+0027 ' , U+2018 ‘ , U+2019 ’) are
 *   NEVER touched, in any position. Possessives and elisions are untouchable
 *   by construction, not by a heuristic that can be fooled.
 *
 * Targeting is grounded in the live corpus, not the stale JSON fallback.
 * Turso `posts` on 2026-08-15, 4,782 bodies with content:
 *   leading U+0022 "  ×147   (146 of them have a partner later in the body)
 *   leading U+201C “  ×7     (all 7 close with U+201D ” — balanced pull quotes)
 *   leading U+2018 ‘  ×1     ('Orderly retreat' — a legitimate quoted phrase)
 *   leading U+0027 '  ×1
 * Exactly ONE body in production is an unpartnered leading double quote. The
 * rule strips that one and leaves the other 153 leading-double bodies intact.
 *
 * Defect D moves the normaliser out of the component into this shared module,
 * so the import path below is part of the contract.
 */

import { describe, expect, it } from "vitest";

import { normalisePostContent } from "../lib/newsfeedText";

// Code points spelled out so an editor's smart-quote pass cannot silently
// rewrite the fixtures into a different character.
const DQ = '"'; // "  QUOTATION MARK
const LDQ = "“"; // “ LEFT DOUBLE QUOTATION MARK
const RDQ = "”"; // ” RIGHT DOUBLE QUOTATION MARK
const APOS = "'"; // '  APOSTROPHE
const LSQ = "‘"; // ‘ LEFT SINGLE QUOTATION MARK
const RSQ = "’"; // ’ RIGHT SINGLE QUOTATION MARK

const SINGLE_GLYPHS = [APOS, LSQ, RSQ];

/** The real unpartnered artifact, Turso post c69SsbQeTr (truncated). */
const PROD_ARTIFACT = `${DQ}Today, we estimate that only ~45% of the S&P 500 by weight is eligible to repurchase shares.`;
/** A real balanced body, Turso post cqVrnbgK0e. */
const PROD_BALANCED = `${DQ}The bond market is yelling from the rooftops that inflation is about to soar.${DQ}`;
/** A real curly-quoted body, Turso post cgTxxHQbiV (truncated). */
const PROD_CURLY_BALANCED = `${LDQ}The real issue here is the liquidity mismatch.${RDQ}`;

type Case = {
  name: string;
  input: string;
  expected: string;
};

const CASES: Case[] = [
  {
    name: "production artifact: unpartnered straight double quote is stripped",
    input: PROD_ARTIFACT,
    expected:
      "Today, we estimate that only ~45% of the S&P 500 by weight is eligible to repurchase shares.",
  },
  {
    name: "production balanced body: both straight double quotes survive",
    input: PROD_BALANCED,
    expected: PROD_BALANCED,
  },
  {
    name: "production curly balanced body: U+201C … U+201D survives",
    input: PROD_CURLY_BALANCED,
    expected: PROD_CURLY_BALANCED,
  },
  {
    name: "unpartnered curly open double quote is stripped",
    input: `${LDQ}The real issue here is the liquidity mismatch`,
    expected: "The real issue here is the liquidity mismatch",
  },
  {
    name: "plural possessive, BALANCED: nothing is stripped and nothing dangles",
    input: `${DQ}The traders${APOS} book is long gamma.${DQ}`,
    expected: `${DQ}The traders${APOS} book is long gamma.${DQ}`,
  },
  {
    name: "plural possessive, UNBALANCED: only the dangling opener goes",
    input: `${DQ}The traders${APOS} book is long gamma.`,
    expected: `The traders${APOS} book is long gamma.`,
  },
  {
    name: "curly plural possessive, BALANCED: untouched",
    input: `${LDQ}The traders${RSQ} book is long gamma.${RDQ}`,
    expected: `${LDQ}The traders${RSQ} book is long gamma.${RDQ}`,
  },
  {
    name: "'Tis elision with a straight apostrophe is never eaten",
    input: `${APOS}Tis the season for gamma`,
    expected: `${APOS}Tis the season for gamma`,
  },
  {
    name: "’Tis elision with a curly apostrophe is never eaten",
    input: `${RSQ}Tis the season for gamma`,
    expected: `${RSQ}Tis the season for gamma`,
  },
  {
    name: "leading single quote around a quoted phrase is never eaten",
    input: `${LSQ}Orderly retreat${RSQ} for hyperscalers?`,
    expected: `${LSQ}Orderly retreat${RSQ} for hyperscalers?`,
  },
  {
    name: "body with no quotes at all is returned unchanged",
    input: "Dealers are short gamma into the close.",
    expected: "Dealers are short gamma into the close.",
  },
  {
    name: "empty body stays empty",
    input: "",
    expected: "",
  },
  {
    name: "a body that is only a double quote character normalises to empty",
    input: DQ,
    expected: "",
  },
  {
    name: "a body that is only an apostrophe is left alone",
    input: APOS,
    expected: APOS,
  },
  {
    name: "nested inner quotes keep the opener — the outer pair is real",
    // The old parity rule stripped this (three glyphs, odd count). The
    // conservative rule keeps it: a later double-quote proves the opener
    // is partnered somewhere, and guessing which pair is which is exactly
    // the inversion this defect is about.
    input: `${DQ}He said ${DQ}yes${DQ} and left`,
    expected: `${DQ}He said ${DQ}yes${DQ} and left`,
  },
  {
    name: "guillemets are outside the double-quote class and are never stripped",
    input: "«Le marche est calme",
    expected: "«Le marche est calme",
  },
  {
    name: "CRLF is normalised and runaway blank lines collapse to one",
    input: "a\r\n\r\n\r\n\r\nb",
    expected: "a\n\nb",
  },
  {
    name: "surrounding whitespace is trimmed before the leading glyph is judged",
    input: `  ${DQ}Buyback bid is coming back\n\n`,
    expected: "Buyback bid is coming back",
  },
  {
    name: "an apostrophe body that also opens with a partnered double quote is untouched",
    input: `${DQ}It doesn${RSQ}t matter, the SEC${APOS}s data shows.${DQ}`,
    expected: `${DQ}It doesn${RSQ}t matter, the SEC${APOS}s data shows.${DQ}`,
  },
];

function countGlyph(text: string, glyph: string): number {
  return text.split(glyph).length - 1;
}

describe("normalisePostContent — conservative unpartnered-double-quote rule", () => {
  for (const { name, input, expected } of CASES) {
    it(name, () => {
      expect(normalisePostContent(input)).toBe(expected);
    });
  }

  it("never alters a single-quote or apostrophe glyph, in any position", () => {
    for (const { name, input } of CASES) {
      const output = normalisePostContent(input);
      for (const glyph of SINGLE_GLYPHS) {
        expect(
          countGlyph(output, glyph),
          `${name}: U+${glyph.codePointAt(0)!.toString(16).toUpperCase().padStart(4, "0")} count changed`,
        ).toBe(countGlyph(input, glyph));
      }
    }
  });

  it("never alters a single-quote glyph placed at the very front, middle or end", () => {
    for (const glyph of SINGLE_GLYPHS) {
      expect(normalisePostContent(`${glyph}front of the line`)).toBe(`${glyph}front of the line`);
      expect(normalisePostContent(`mid${glyph}line word`)).toBe(`mid${glyph}line word`);
      expect(normalisePostContent(`traders${glyph} book`)).toBe(`traders${glyph} book`);
      expect(normalisePostContent(`end of line${glyph}`)).toBe(`end of line${glyph}`);
    }
  });

  it("is idempotent — a second pass changes nothing", () => {
    for (const { name, input } of CASES) {
      const once = normalisePostContent(input);
      expect(normalisePostContent(once), name).toBe(once);
    }
  });
});

/**
 * The single-quote branch. A lone leading apostrophe is lexically identical in
 * "'Tis the season" and "'Short-dated SPX realized vol remains…", so parity
 * cannot separate them. The truncated pull-quote is separated instead by its
 * real signature: the body opens by repeating the post's own headline.
 *
 * The live case is the one on screen in the operator's report — Turso post
 * "Short-dated SPX realized vol", body first code point U+0027, unpartnered.
 */
describe("normalisePostContent — headline-echo rule for a leading single quote", () => {
  const LIVE_TITLE = "Short-dated SPX realized vol";
  const LIVE_BODY = `${APOS}Short-dated SPX realized vol remains pinned near the floor, supported by heavy dealer gamma.`;

  it("strips the leading apostrophe when the body echoes the headline", () => {
    expect(normalisePostContent(LIVE_BODY, LIVE_TITLE)).toBe(
      "Short-dated SPX realized vol remains pinned near the floor, supported by heavy dealer gamma.",
    );
  });

  it("leaves a 'Tis elision alone when the headline does not match", () => {
    expect(normalisePostContent(`${APOS}Tis the season for gamma`, "Volatility outlook")).toBe(
      `${APOS}Tis the season for gamma`,
    );
    expect(normalisePostContent(`${RSQ}Tis the season for gamma`, "Volatility outlook")).toBe(
      `${RSQ}Tis the season for gamma`,
    );
  });

  it("leaves the body alone when a second apostrophe partners the opener", () => {
    // A quoted headline echo is a real quotation, not a truncation artifact.
    const quoted = `${APOS}Short-dated SPX realized vol${APOS} is the desk view`;
    expect(normalisePostContent(quoted, LIVE_TITLE)).toBe(quoted);
  });

  it("never strips a possessive, whatever the headline", () => {
    const possessive = "The traders' book is long gamma.";
    expect(normalisePostContent(possessive, "The traders' book")).toBe(possessive);
  });

  it("does nothing without a title, so the branch is opt-in", () => {
    expect(normalisePostContent(LIVE_BODY)).toBe(LIVE_BODY);
    expect(normalisePostContent(LIVE_BODY, "")).toBe(LIVE_BODY);
  });

  it("matches the headline case-insensitively and across whitespace runs", () => {
    expect(normalisePostContent(`${APOS}SHORT-DATED   SPX realized vol holds`, LIVE_TITLE)).toBe(
      "SHORT-DATED   SPX realized vol holds",
    );
  });

  it("is idempotent on the live case", () => {
    const once = normalisePostContent(LIVE_BODY, LIVE_TITLE);
    expect(normalisePostContent(once, LIVE_TITLE)).toBe(once);
  });
});
