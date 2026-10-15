// R-310 (continued): /api/share/pnl is in PUBLIC_SHARE_API_ROUTES, so an
// anonymous caller chooses the strings satori lays out. The route read
// `description`, `holdTime` and `time` straight out of the query string with no
// length bound, and the card can only ever display a line of them, so every
// character past that bound buys nothing but layout time on the single-threaded
// Next.js process that also serves the trading UI. `clampCardText` is the one
// place every query-supplied string crosses into the render tree.

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

describe("clampCardText", () => {
  it("leaves an ordinary contract description byte-identical", () => {
    const ordinary = "Closed AAOI (Long $115 Call)";
    expect(clampCardText(ordinary)).toBe(ordinary);
  });

  it("caps a multi-kilobyte string at the display bound", () => {
    const clamped = clampCardText("A".repeat(12_000));
    expect(clamped.length).toBe(SHARE_PNL_MAX_TEXT_CHARS);
  });

  it("drops control characters, which carry no glyph", () => {
    expect(clampCardText("Closed\u0000 AAOI\u001b\u009b")).toBe("Closed  AAOI");
  });

  it("bounds the display budget well below a kilobyte", () => {
    expect(SHARE_PNL_MAX_TEXT_CHARS).toBeGreaterThan(0);
    expect(SHARE_PNL_MAX_TEXT_CHARS).toBeLessThanOrEqual(200);
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

  it("rejects a description made only of control characters", async () => {
    const res = await GET(
      new Request(`https://radon.run/api/share/pnl?description=${encodeURIComponent("\u0000\u0001")}`),
    );
    expect(res.status).toBe(400);
  });
});
