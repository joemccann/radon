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
