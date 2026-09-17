import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { SHARE_PNL_HERO_PX, sharePnlHeroLayout } from "../lib/sharePnlHero";

const ROUTE = readFileSync(resolve(__dirname, "../app/api/share/pnl/route.tsx"), "utf8");

describe("share P&L hero when $ and % are both on", () => {
  it("stacks the dollar above the percent", () => {
    const layout = sharePnlHeroLayout({ dollar: true, pct: true });
    expect(layout.direction).toBe("column");
    expect(layout.pctFontSizePx).toBe(layout.dollarFontSizePx / 2);
    expect(layout.pctFontSizePx).toBeLessThan(layout.dollarFontSizePx);
  });

  it("keeps a solo metric at full hero size", () => {
    expect(sharePnlHeroLayout({ dollar: true, pct: false })).toEqual({
      direction: "column",
      dollarFontSizePx: SHARE_PNL_HERO_PX,
      pctFontSizePx: SHARE_PNL_HERO_PX,
    });
    expect(sharePnlHeroLayout({ dollar: false, pct: true }).pctFontSizePx).toBe(SHARE_PNL_HERO_PX);
  });

  it("wires the stacked sizes through the share image route", () => {
    expect(ROUTE).toContain("sharePnlHeroLayout");
    expect(ROUTE).not.toContain('heroPct ? "92px"');
    expect(ROUTE).not.toContain('heroDollar ? "86px"');
    expect(ROUTE).not.toContain("marginLeft: heroDollar");
  });
});
