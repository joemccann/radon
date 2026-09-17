/** Solo $ or % hero size on the 1200x630 share card. */
export const SHARE_PNL_HERO_PX = 192;

/**
 * Stacked dollar size when $ and % both render. Narrower than the solo
 * hero so a figure like +$27,460.74 still fits the padded 1200px canvas.
 */
export const SHARE_PNL_STACKED_DOLLAR_PX = 160;

export function sharePnlHeroLayout(opts: { dollar: boolean; pct: boolean }): {
  direction: "column";
  dollarFontSizePx: number;
  pctFontSizePx: number;
} {
  const stacked = opts.dollar && opts.pct;
  const dollarFontSizePx = stacked ? SHARE_PNL_STACKED_DOLLAR_PX : SHARE_PNL_HERO_PX;
  return {
    direction: "column",
    dollarFontSizePx,
    pctFontSizePx: stacked ? dollarFontSizePx / 2 : SHARE_PNL_HERO_PX,
  };
}
