// R-310 / R-311 (REL-105): the share-image pipeline is bounded, and its font
// cache cannot be poisoned.
//
// (a) The CTA image route derives its canvas height directly from the row
//     count read out of a cache FILE — `50 + sections*64 + rows*28 + 20` —
//     with no cap on either. A malformed or oversized extraction asks
//     ImageResponse for a canvas tens of thousands of pixels tall, and the
//     route declares no maxDuration.
// (b) `loadFonts` assigns `fontRegular` BEFORE reading the Bold face. If the
//     Bold read rejects, the regular is already cached, so every later call
//     skips the load block entirely and returns a Bold entry with
//     `data: null` — permanently, from one transient failure.

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import { computeCtaImageHeight, MAX_IMAGE_HEIGHT, MAX_IMAGE_ROWS } from "@/lib/ctaImageLayout";

describe("(a) the CTA plate canvas is bounded", () => {
  it("sizes an ordinary payload exactly as before", () => {
    // 3 sections, 30 rows: 50 + 3*64 + 30*28 + 20 = 1102
    expect(computeCtaImageHeight({ sectionCount: 3, totalRows: 30 })).toBe(1102);
  });

  it("clamps a 5,000-row extraction instead of asking for a 140,000px canvas", () => {
    const height = computeCtaImageHeight({ sectionCount: 3, totalRows: 5000 });
    expect(height).toBeLessThanOrEqual(MAX_IMAGE_HEIGHT);
  });

  it("never returns a non-finite or negative height", () => {
    for (const bad of [Number.NaN, Infinity, -1, -Infinity]) {
      const h = computeCtaImageHeight({ sectionCount: bad, totalRows: bad });
      expect(Number.isFinite(h)).toBe(true);
      expect(h).toBeGreaterThan(0);
      expect(h).toBeLessThanOrEqual(MAX_IMAGE_HEIGHT);
    }
  });

  it("caps the rows a single plate will lay out", () => {
    expect(MAX_IMAGE_ROWS).toBeGreaterThan(0);
    expect(MAX_IMAGE_ROWS).toBeLessThan(5000);
  });
});

describe("(a) every satori image route declares a duration bound", () => {
  // The sibling routes size their canvas from constants (500 / 630), so the
  // unbounded-HEIGHT defect is the CTA route's alone — but all three run the
  // same Node-runtime satori render and none of them bounded its duration.
  const ROUTES = [
    "app/api/menthorq/cta/image/route.tsx",
    "app/api/menthorq/[command]/image/route.tsx",
    "app/api/share/pnl/route.tsx",
  ];

  it.each(ROUTES)("%s exports maxDuration", async (rel) => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const raw = readFileSync(resolve(__dirname, "..", rel), "utf8");
    const src = raw
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split("\n")
      .filter((l) => !l.trim().startsWith("//"))
      .join("\n");
    expect(src).toMatch(/export const maxDuration\s*=/);
  });
});

describe("(b) the OG font cache cannot be poisoned by a partial load", () => {
  const files: Record<string, unknown> = {};

  beforeEach(() => {
    vi.resetModules();
    vi.doMock("fs/promises", () => ({
      readFile: vi.fn(async (p: string) => {
        const key = String(p).includes("Bold") ? "bold" : "regular";
        const v = files[key];
        if (v instanceof Error) throw v;
        return v;
      }),
    }));
  });

  afterEach(() => {
    vi.doUnmock("fs/promises");
    vi.resetModules();
  });

  it("retries the load after a failed Bold read instead of caching a null face", async () => {
    files.regular = Buffer.from("regular");
    files.bold = new Error("ENOENT: IBMPlexMono-Bold.woff");

    const { loadFonts } = await import("@/lib/og-fonts");
    await expect(loadFonts()).rejects.toThrow();

    // The Bold file comes back. The SECOND call must retry, not serve a
    // permanently-cached `data: null` Bold.
    files.bold = Buffer.from("bold");
    const fonts = await loadFonts();
    expect(fonts.every((f) => f.data != null)).toBe(true);
    expect(fonts.find((f) => f.weight === 700)?.data).toEqual(Buffer.from("bold"));
  });

  it("still caches after a fully successful load", async () => {
    files.regular = Buffer.from("regular");
    files.bold = Buffer.from("bold");

    const fsMod = await import("fs/promises");
    const { loadFonts } = await import("@/lib/og-fonts");
    await loadFonts();
    const callsAfterFirst = (fsMod.readFile as ReturnType<typeof vi.fn>).mock.calls.length;
    await loadFonts();
    expect((fsMod.readFile as ReturnType<typeof vi.fn>).mock.calls.length).toBe(
      callsAfterFirst,
    );
  });
});
