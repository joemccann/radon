// R-310 / R-311 (REL-105): the share-image pipeline is bounded, and its font
// cache cannot be poisoned.
//
// (a) The satori render route declares a duration bound.
// (b) `loadFonts` assigns `fontRegular` BEFORE reading the Bold face. If the
//     Bold read rejects, the regular is already cached, so every later call
//     skips the load block entirely and returns a Bold entry with
//     `data: null` — permanently, from one transient failure.

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

describe("(a) every satori image route declares a duration bound", () => {
  const ROUTES = [
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
