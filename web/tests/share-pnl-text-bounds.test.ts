// R-310 (continued): /api/share/pnl is in PUBLIC_SHARE_API_ROUTES, so an
// anonymous caller chooses the strings satori lays out. The route read
// `description`, `holdTime` and `time` straight out of the query string with no
// length bound and no character-class filter, and both halves cost:
//
//  (a) LENGTH. The card lays out every character it is given. A multi-kilobyte
//      description measured two orders of magnitude over a real render on the
//      single-threaded process that also serves the trading UI.
//  (b) CHARACTER CLASS. `next/og` installs satori's `loadAdditionalAsset`
//      unconditionally, so every grapheme the bundled fonts cannot resolve
//      makes the render reach OUT to cdn.jsdelivr.net (emoji) or
//      fonts.googleapis.com (any other script) and caches the result in a
//      module-level Map keyed on the caller's own text. The bundled
//      IBMPlexMono faces carry 229 code points, all of printable ASCII among
//      them, so restricting the text to printable ASCII means no render can be
//      made to fetch anything.
//
// `clampCardText` is the one place every query-supplied string crosses into the
// render tree.

import type { ReactNode, ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SHARE_PNL_MAX_TEXT_CHARS, clampCardText } from "../lib/sharePnlHero";

const rendered = vi.hoisted(() => ({ tree: null as ReactNode }));
vi.mock("next/og", () => ({
  ImageResponse: class extends Response {
    constructor(tree: ReactNode) {
      super("png");
      rendered.tree = tree;
    }
  },
}));
vi.mock("@/lib/og-fonts", () => ({ loadFonts: async () => [] }));
vi.mock("@/lib/rateLimit", () => ({
  rateLimit: () => ({ ok: true }),
  clientIp: () => "test",
  SHARE_PNL_LIMIT: 10,
  SHARE_WINDOW_MS: 60_000,
}));

import { GET } from "../app/api/share/pnl/route";

function text(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join(" ");
  return text((node as ReactElement<{ children?: ReactNode }>).props?.children);
}

/** Every string the render tree holds, which is what satori has to lay out. */
function strings(node: ReactNode): string[] {
  if (typeof node === "string") return [node];
  if (node == null || typeof node === "boolean" || typeof node === "number") return [];
  if (Array.isArray(node)) return node.flatMap(strings);
  return strings((node as ReactElement<{ children?: ReactNode }>).props?.children ?? null);
}

/** Longest string the render tree holds, which is what satori has to lay out. */
function longestString(node: ReactNode): number {
  if (typeof node === "string") return node.length;
  if (node == null || typeof node === "boolean" || typeof node === "number") return 0;
  if (Array.isArray(node)) return Math.max(0, ...node.map(longestString));
  const el = node as ReactElement<{ children?: ReactNode }>;
  return longestString(el.props?.children ?? null);
}

afterEach(() => {
  rendered.tree = null;
});

/** Everything the clamp is allowed to emit: printable ASCII and the ellipsis
 *  it appends, which the bundled faces do carry (U+2026). */
const EMITTABLE = /^[\x20-\x7e\u2026]*$/;

describe("clampCardText", () => {
  it("leaves an ordinary contract description byte-identical", () => {
    const ordinary = "Closed AAOI (Long $115 Call)";
    expect(clampCardText(ordinary)).toBe(ordinary);
  });

  it("leaves a real multi-leg close untruncated", () => {
    // The widest description the app itself produces: a mixed-expiry 5-leg
    // close, from buildExecutedGroupDescription.
    const fiveLeg =
      "Closed TSLA 5-Leg Combo (Short $420.5 Put 9/18 / Long $430.5 Put 10/16 /" +
      " Short $440.5 Call 11/20 / Long $450.5 Call 12/18 / Long $460.5 Call 1/15)";
    expect(fiveLeg.length).toBeGreaterThan(120);
    expect(clampCardText(fiveLeg)).toBe(fiveLeg);
  });

  it("caps a multi-kilobyte string at the cost bound", () => {
    const clamped = clampCardText("A".repeat(12_000));
    expect(clamped.length).toBe(SHARE_PNL_MAX_TEXT_CHARS);
  });

  it("drops control characters, which carry no glyph", () => {
    expect(clampCardText("Closed\u0000 AAOI\u001b\u009b")).toBe("Closed  AAOI");
  });

  it("drops every code point the bundled faces cannot resolve", () => {
    // Each of these would otherwise make the render fetch a remote asset.
    expect(clampCardText("a\u{1f600}b")).toBe("a b");
    expect(clampCardText("a\u4e2db")).toBe("a b");
    expect(clampCardText("a\u200db")).toBe("a b");
    expect(clampCardText("caf\u00e9")).toBe("caf");
  });

  it("never emits a lone surrogate when it truncates", () => {
    const out = clampCardText("\u{1f600}".repeat(400) + "A".repeat(400));
    expect(out).toMatch(EMITTABLE);
    expect(out.isWellFormed()).toBe(true);
  });

  it("emits nothing the bundled faces do not cover, whatever it is given", () => {
    for (const input of [
      "A".repeat(5_000),
      "\u{1f680}\u{1f4c8}\u0e01\u05d0\u0627",
      "\u2028\u2029\ufeff\ufffd",
      "\u{10ffff}",
    ]) {
      expect(clampCardText(input)).toMatch(EMITTABLE);
    }
  });

  it("bounds the cost budget far below a kilobyte", () => {
    expect(SHARE_PNL_MAX_TEXT_CHARS).toBeGreaterThanOrEqual(200);
    expect(SHARE_PNL_MAX_TEXT_CHARS).toBeLessThanOrEqual(512);
  });
});

describe("the share card never lays out an unbounded query string", () => {
  for (const format of ["card", "story"] as const) {
    it(`${format} truncates every query-supplied string before layout`, async () => {
      const params = new URLSearchParams({
        description: "A".repeat(12_000),
        holdTime: "B".repeat(12_000),
        time: "C".repeat(12_000),
        pnl: "11435.65",
        pnlPct: "44.08",
        format,
      });
      const res = await GET(new Request(`https://radon.run/api/share/pnl?${params}`));
      expect(res.status).toBe(200);
      expect(longestString(rendered.tree)).toBeLessThanOrEqual(SHARE_PNL_MAX_TEXT_CHARS);
    });
  }

  it("still renders a legitimate card unchanged", async () => {
    const params = new URLSearchParams({
      description: "Closed AAOI (Long $115 Call)",
      pnl: "27460.74",
      pnlPct: "44.08",
      entryPrice: "5.59",
      exitPrice: "8.05",
      holdTime: "2 days",
    });
    expect((await GET(new Request(`https://radon.run/api/share/pnl?${params}`))).status).toBe(200);
    const output = text(rendered.tree);
    expect(output).toContain("Closed AAOI (Long $115 Call)");
    expect(output).toContain("+$27,460.74");
    expect(output).toContain("+44.08%");
    expect(output).toContain("HELD 2 days");
  });

  it("never sends a string into the tree that could trigger a remote asset fetch", async () => {
    const params = new URLSearchParams({
      description: "\u{1f600}".repeat(200),
      holdTime: "\u4e2d".repeat(200),
      time: "\u200d".repeat(200),
      pnl: "1",
      format: "story",
    });
    expect((await GET(new Request(`https://radon.run/api/share/pnl?${params}`))).status).toBe(200);
    for (const s of strings(rendered.tree)) {
      expect(s).toMatch(EMITTABLE);
    }
  });

  it("keeps 400 for an absent description and renders a blank one as before", async () => {
    expect((await GET(new Request("https://radon.run/api/share/pnl?pnl=1"))).status).toBe(400);
    // A whitespace-only contract_desc reaches this route from the blotter share
    // path; it rendered a card with an empty description line and still must.
    const res = await GET(
      new Request(`https://radon.run/api/share/pnl?pnl=1&description=${encodeURIComponent("  ")}`),
    );
    expect(res.status).toBe(200);
  });
});
