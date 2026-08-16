/**
 * Newsfeed body text normalisation — the single source of truth.
 *
 * Applied once, at the shared `useNewsfeedPosts` boundary, so the rail, the
 * lightbox, the bookmarks snapshot and the profile list all render the same
 * bytes.
 *
 * Why the rule is this conservative: themarketear pull-quote excerpts
 * occasionally truncate mid-quotation and ship an opening double quote with no
 * closing partner. A parity count over every quote glyph looks like the general
 * answer and is not — a plural possessive ("the traders' book") or an elision
 * ("'Tis") flips the count and strips a legitimate opener, which is worse than
 * leaving the artifact alone.
 *
 * Double quotes are settled on parity alone. Single quotes are not decidable
 * that way — "'Tis the season" and "'Short-dated SPX realized vol remains…"
 * are lexically identical, both a lone opener followed by a capitalised word.
 * They are separated instead by the artifact's real signature: the truncated
 * pull-quote opens by repeating the post's own headline. So a leading single
 * quote is dropped only when the body it introduces starts with the title.
 * An apostrophe anywhere other than index 0 is never touched.
 *
 * Production census, Turso `posts` 2026-08-15 (4,782 bodies with content):
 *   leading U+0022 "  ×147  (146 partnered later in the body)
 *   leading U+201C “  ×7    (all 7 partnered)
 *   leading U+2018 ‘  ×1
 *   leading U+0027 '  ×1    (unpartnered — "Short-dated SPX realized vol")
 * Two bodies change: the unpartnered double (`c69SsbQeTr`) and the single-quote
 * headline echo. Those are the artifacts this exists for.
 */

/** U+0022, U+201C, U+201D. */
const DOUBLE_QUOTE_GLYPHS = ['"', "“", "”"];
/** U+0027, U+2018, U+2019. */
const SINGLE_QUOTE_GLYPHS = ["'", "‘", "’"];

function isDoubleQuoteGlyph(character: string): boolean {
  return DOUBLE_QUOTE_GLYPHS.includes(character);
}

function isSingleQuoteGlyph(character: string): boolean {
  return SINGLE_QUOTE_GLYPHS.includes(character);
}

function collapseScrapeWhitespace(raw: string): string {
  return raw.replace(/\r\n?/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

function opensWithUnpartneredDoubleQuote(text: string): boolean {
  if (!isDoubleQuoteGlyph(text.charAt(0))) return false;
  return !Array.from(text.slice(1)).some(isDoubleQuoteGlyph);
}

/** Loose comparison key: case and inner whitespace runs must not decide this. */
function headlineKey(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

/** True when the body, minus its opening single quote, echoes the headline —
 *  the truncated pull-quote signature. An elision like "'Tis" fails this. */
function opensWithHeadlineEcho(text: string, title: string): boolean {
  if (!isSingleQuoteGlyph(text.charAt(0))) return false;
  if (Array.from(text.slice(1)).some(isSingleQuoteGlyph)) return false;
  const headline = headlineKey(title);
  if (headline.length === 0) return false;
  return headlineKey(text.slice(1)).startsWith(headline);
}

/**
 * Normalise line endings, collapse runaway blank lines, and drop a scrape
 * artifact opener: an unpartnered double quote, or a single quote whose body
 * echoes `title`. Pass the post's own title; omitting it disables the
 * single-quote branch entirely.
 */
export function normalisePostContent(raw: string, title = ""): string {
  const text = collapseScrapeWhitespace(raw);
  if (opensWithUnpartneredDoubleQuote(text)) return text.slice(1).trimStart();
  if (opensWithHeadlineEcho(text, title)) return text.slice(1).trimStart();
  return text;
}
