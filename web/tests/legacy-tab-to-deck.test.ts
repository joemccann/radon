// Pure unit test for the legacy `?tab=` → `?deck=` back-compat shim.
//
// DEPENDS ON NOT-YET-PRESENT SOURCE: `web/lib/legacyTabToDeck.ts`, written by the
// navigation track. This test is correct as written and goes green the moment
// that file lands. Contract (from the IA overhaul plan §2):
//   - legacyTabToDeck(tab) -> DeckKey | null
//   - hot-path / docked surfaces map to null (book is always visible, ticket is
//     always docked): book | company | order | null  -> null
//   - reference surfaces open a deck:
//       chain       -> "c"
//       position    -> "p"
//       news        -> "n"
//       ratings     -> "r"
//       seasonality -> "s"
//   - any unknown string -> null (old links never 404 into a blank state)
//   - VALID_DECKS is the membership set of deck keys.

import { describe, expect, it } from "vitest";

import { legacyTabToDeck, VALID_DECKS } from "../lib/legacyTabToDeck";

// VALID_DECKS is typed to accept only DeckKey; the membership guard exists to be
// queried with arbitrary runtime strings, so widen it for the test assertions.
const isValidDeck = (value: string): boolean =>
  (VALID_DECKS as ReadonlySet<string>).has(value);

describe("legacyTabToDeck — hot-path / docked tabs collapse to no deck", () => {
  it("maps book → null (the book is always visible)", () => {
    expect(legacyTabToDeck("book")).toBeNull();
  });

  it("maps company → null (company is demoted into the `i` deck but the legacy default lands book-first)", () => {
    expect(legacyTabToDeck("company")).toBeNull();
  });

  it("maps order → null (the ticket is always docked in the Act column)", () => {
    expect(legacyTabToDeck("order")).toBeNull();
  });

  it("maps null/empty → null (no tab param = book-first default)", () => {
    expect(legacyTabToDeck(null)).toBeNull();
    expect(legacyTabToDeck("")).toBeNull();
  });
});

describe("legacyTabToDeck — reference tabs open the matching deck", () => {
  it("maps chain → c", () => {
    expect(legacyTabToDeck("chain")).toBe("c");
  });

  it("maps position → p", () => {
    expect(legacyTabToDeck("position")).toBe("p");
  });

  it("maps news → n", () => {
    expect(legacyTabToDeck("news")).toBe("n");
  });

  it("maps ratings → r", () => {
    expect(legacyTabToDeck("ratings")).toBe("r");
  });

  it("maps seasonality → s", () => {
    expect(legacyTabToDeck("seasonality")).toBe("s");
  });
});

describe("legacyTabToDeck — unknown input", () => {
  it("maps an unrecognized tab → null", () => {
    expect(legacyTabToDeck("nope")).toBeNull();
    expect(legacyTabToDeck("CHAIN")).toBeNull(); // case-sensitive: not the lowercase key
    expect(legacyTabToDeck("greeks")).toBeNull();
  });
});

describe("VALID_DECKS membership", () => {
  it("contains exactly the deck keys a reference tab can map to", () => {
    // Every non-null mapping target must be a member of VALID_DECKS.
    for (const key of ["c", "p", "n", "r", "s"]) {
      expect(isValidDeck(key)).toBe(true);
    }
  });

  it("contains the Equibles reference decks so ?deck=h / ?deck=f survive a reload", () => {
    expect(isValidDeck("h")).toBe(true);
    expect(isValidDeck("f")).toBe(true);
  });

  it("rejects non-deck strings", () => {
    expect(isValidDeck("book")).toBe(false);
    expect(isValidDeck("company")).toBe(false);
    expect(isValidDeck("order")).toBe(false);
    expect(isValidDeck("")).toBe(false);
  });

  it("every reference-tab mapping result is a VALID_DECKS member", () => {
    for (const tab of ["chain", "position", "news", "ratings", "seasonality"]) {
      const deck = legacyTabToDeck(tab);
      expect(deck).not.toBeNull();
      expect(isValidDeck(deck as string)).toBe(true);
    }
  });
});
