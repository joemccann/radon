import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../app/globals.css"), "utf8");

function declarationBlock(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  expect(match, `missing CSS block for ${selector}`).not.toBeNull();
  return match?.[1] ?? "";
}

/**
 * The order-ticket-rail design (Claude Design `Canvas.dc.html`, exploration 1a) fills its
 * signal / caution / fault badges with translucent washes. The design ships them as raw
 * rgba, which cannot shift between themes - a baked rgba stays the dark-theme colour on a
 * white canvas. Derive them from the existing semantic tokens with color-mix instead, so
 * one definition tracks both themes and the repo's contrast-tuned values.
 */
describe("translucent signal washes", () => {
  const root = declarationBlock(":root");

  it("defines the three washes the ticket rail needs", () => {
    expect(root).toContain("--wash-signal:");
    expect(root).toContain("--wash-warn:");
    expect(root).toContain("--wash-fault:");
  });

  it("derives each wash from its semantic token via color-mix, never a baked rgba", () => {
    const washes = [...root.matchAll(/--wash-[a-z]+:\s*([^;]+);/g)].map((m) => m[1].trim());
    expect(washes.length).toBeGreaterThanOrEqual(3);
    for (const value of washes) {
      expect(value).toMatch(/^color-mix\(in srgb, var\(--[a-z-]+\)/);
      expect(value).toContain("transparent");
      expect(value).not.toMatch(/rgba?\(/);
    }
  });

  it("mixes against the token each wash is named for", () => {
    expect(root).toMatch(/--wash-signal:\s*color-mix\(in srgb, var\(--signal-core\)/);
    expect(root).toMatch(/--wash-warn:\s*color-mix\(in srgb, var\(--warn\)/);
    expect(root).toMatch(/--wash-fault:\s*color-mix\(in srgb, var\(--fault\)/);
  });
});
