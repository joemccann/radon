/**
 * Deck model for the asset cockpit.
 *
 * The cockpit replaces the old 8-tab ticker view. The hot path (book, ticket,
 * position) is always docked, so it has no deck key. Reference surfaces open a
 * deck over the Act column:
 *   c = chain, p = position (expand), n = news, r = ratings, s = seasonality,
 *   i = info, h = 13F holders, f = filing forensics
 */
export type DeckKey = "c" | "p" | "n" | "r" | "s" | "i" | "h" | "f";

export const VALID_DECKS: ReadonlySet<DeckKey> = new Set<DeckKey>([
  "c",
  "p",
  "n",
  "r",
  "s",
  "i",
  "h",
  "f",
]);

/**
 * Single source of truth for "is this string a URL-addressable deck key?".
 * Shared by TickerWorkspace (reads `?deck=`) and TickerDetailContent (derives
 * the active deck from the threaded prop) so the two never disagree on the
 * deck-key contract again. Note `":"` (command palette) is deliberately NOT a
 * deck key here: it is local-only state, never URL-addressable.
 */
export function isDeckKey(value: string | null | undefined): value is DeckKey {
  return value != null && VALID_DECKS.has(value as DeckKey);
}

/**
 * Map a legacy `?tab=` value to the new deck model so old links/bookmarks
 * resolve instead of landing on a blank state.
 *
 * - book | company | order | undefined → null (no deck: the book is always
 *   visible and the ticket is always docked, so these need no overlay)
 * - chain → "c", position → "p", news → "n", ratings → "r", seasonality → "s"
 */
export function legacyTabToDeck(tab: string | null): DeckKey | null {
  switch (tab) {
    case "chain":
      return "c";
    case "position":
      return "p";
    case "news":
      return "n";
    case "ratings":
      return "r";
    case "seasonality":
      return "s";
    default:
      return null;
  }
}
