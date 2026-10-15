/** Solo $ or % hero size on the 1200x630 share card. */
export const SHARE_PNL_HERO_PX = 192;

/**
 * Stacked dollar size when $ and % both render. Narrower than the solo
 * hero so a figure like +$27,460.74 still fits the padded 1200px canvas.
 */
export const SHARE_PNL_STACKED_DOLLAR_PX = 160;

/**
 * Instagram Story hero sizes. The 1080px story canvas has a 936px content
 * column (72px gutters), so the dollar is capped at 128px: IBM Plex Mono's
 * 0.6em advance puts +$127,460.74 at ~922px.
 */
export const SHARE_PNL_STORY_DOLLAR_PX = 128;
export const SHARE_PNL_STORY_PCT_PX = 176;
export const SHARE_PNL_STORY_STACKED_PCT_PX = 88;

export type SharePnlFormat = "card" | "story";

/** `card` is the X / Open Graph plate; `story` is Instagram's 9:16 frame. */
export const SHARE_PNL_CANVAS: Record<SharePnlFormat, { width: number; height: number }> = {
  card: { width: 1200, height: 630 },
  story: { width: 1080, height: 1920 },
};

/**
 * Cost bound on a query-supplied string, not a display bound — the 1200x630
 * plate only shows ~74 characters of 24px IBM Plex Mono, but the app's own
 * widest description (a mixed-expiry 5-leg close) runs to ~145, and a card
 * must never drop a leg of the structure it reports. 400 leaves that headroom
 * while staying three orders of magnitude below the multi-kilobyte strings
 * that made one anonymous request cost ~100x a real render (R-310).
 */
export const SHARE_PNL_MAX_TEXT_CHARS = 400;

/**
 * The one gate every query-supplied string passes through on its way into the
 * render tree. Two bounds, because the renderer charges for both:
 *
 *  - CHARACTER CLASS. `next/og` always installs satori's
 *    `loadAdditionalAsset`, so any grapheme the bundled faces cannot resolve
 *    sends the render to cdn.jsdelivr.net (emoji) or fonts.googleapis.com
 *    (every other script) mid-layout and caches the result in a process-wide
 *    Map keyed on the caller's own text. The bundled IBMPlexMono subsets
 *    carry 229 code points and all of printable ASCII is among them, so
 *    keeping only printable ASCII means no card can be made to fetch
 *    anything. Dropping by code point also means a truncation can never split
 *    a surrogate pair.
 *  - LENGTH. Everything that survives is capped, with an ellipsis (U+2026,
 *    which the faces do carry) so a truncated card reads as truncated.
 */
export function clampCardText(
  value: string,
  maxChars: number = SHARE_PNL_MAX_TEXT_CHARS,
): string {
  const renderable = Array.from(value)
    .map((ch) => {
      const cp = ch.codePointAt(0) ?? 0;
      return cp >= 0x20 && cp <= 0x7e ? ch : " ";
    })
    .join("")
    .trim();
  if (renderable.length <= maxChars) return renderable;
  return `${renderable.slice(0, maxChars - 1)}\u2026`;
}

export function parseSharePnlFormat(value: string | null): SharePnlFormat {
  return value === "story" ? "story" : "card";
}

export function sharePnlHeroLayout(
  opts: { dollar: boolean; pct: boolean },
  format: SharePnlFormat = "card",
): {
  direction: "column";
  dollarFontSizePx: number;
  pctFontSizePx: number;
} {
  const stacked = opts.dollar && opts.pct;
  if (format === "story") {
    return {
      direction: "column",
      dollarFontSizePx: SHARE_PNL_STORY_DOLLAR_PX,
      pctFontSizePx: stacked ? SHARE_PNL_STORY_STACKED_PCT_PX : SHARE_PNL_STORY_PCT_PX,
    };
  }
  const dollarFontSizePx = stacked ? SHARE_PNL_STACKED_DOLLAR_PX : SHARE_PNL_HERO_PX;
  return {
    direction: "column",
    dollarFontSizePx,
    pctFontSizePx: stacked ? dollarFontSizePx / 2 : SHARE_PNL_HERO_PX,
  };
}
