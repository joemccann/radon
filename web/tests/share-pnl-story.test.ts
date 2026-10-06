import { describe, expect, it } from "vitest";
import { GET } from "../app/api/share/pnl/route";
import {
  SHARE_PNL_CANVAS,
  parseSharePnlFormat,
  sharePnlHeroLayout,
} from "../lib/sharePnlHero";

/** PNG IHDR: width at bytes 16..19, height at 20..23 (big-endian). */
function pngSize(buf: ArrayBuffer): { width: number; height: number } {
  const view = new DataView(buf);
  return { width: view.getUint32(16), height: view.getUint32(20) };
}

function req(qs: Record<string, string>): Request {
  return new Request(`https://radon.run/api/share/pnl?${new URLSearchParams(qs)}`, {
    headers: { "x-forwarded-for": `10.9.${Math.floor(Math.random() * 250)}.${Math.floor(Math.random() * 250)}` },
  });
}

const BASE = {
  description: "Closed AAOI (Long $115 Call)",
  pnl: "27460.74",
  pnlPct: "44.08",
  entryPrice: "5.59",
  exitPrice: "8.05",
  entryTime: "2026-03-15T10:30:00-07:00",
  exitTime: "2026-03-18T07:03:53-07:00",
  holdTime: "2 days",
};

describe("share P&L story format", () => {
  it("only accepts the literal 'story'; anything else is the X card", () => {
    expect(parseSharePnlFormat("story")).toBe("story");
    for (const v of [null, "", "card", "STORY", "1080x1920"]) {
      expect(parseSharePnlFormat(v)).toBe("card");
    }
  });

  it("sizes the story canvas to Instagram's 9:16 1080x1920", () => {
    expect(SHARE_PNL_CANVAS.story).toEqual({ width: 1080, height: 1920 });
    expect(SHARE_PNL_CANVAS.card).toEqual({ width: 1200, height: 630 });
  });

  it("keeps the stacked story dollar narrow enough for a six-figure P&L", () => {
    // IBM Plex Mono advance is 0.6em; the story content column is 1080 - 2*72.
    const layout = sharePnlHeroLayout({ dollar: true, pct: true }, "story");
    expect("+$127,460.74".length * 0.6 * layout.dollarFontSizePx).toBeLessThanOrEqual(1080 - 2 * 72);
    expect(layout.pctFontSizePx).toBeLessThan(layout.dollarFontSizePx);
  });

  it("leaves the X card layout untouched by default", () => {
    expect(sharePnlHeroLayout({ dollar: true, pct: true })).toEqual(
      sharePnlHeroLayout({ dollar: true, pct: true }, "card"),
    );
  });

  it("renders a 1080x1920 PNG for format=story", async () => {
    const res = await GET(req({ ...BASE, format: "story" }));
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("image/png");
    expect(pngSize(await res.arrayBuffer())).toEqual({ width: 1080, height: 1920 });
  }, 30_000);

  it("still renders the 1200x630 card without format", async () => {
    const res = await GET(req(BASE));
    expect(res.status).toBe(200);
    expect(pngSize(await res.arrayBuffer())).toEqual({ width: 1200, height: 630 });
  }, 30_000);
});
