/**
 * design.md / radon.css contract — the deterministic checks behind the public
 * agent-design surface (https://app.radon.run/design.md).
 *
 * Modeled on Vercel's design.md system: judgment lives in design.md prose,
 * repeatable mechanics live in the stylesheet, and anything mechanical is
 * checked here so a named failure stays gone once encoded.
 *
 * Three surfaces are pinned:
 *   1. Vocabulary sync — every rd-* class defined in radon.css is documented
 *      in design.md and vice versa. An undocumented class is invisible to
 *      agents; a documented ghost class silently renders unstyled.
 *   2. Brand mechanics in radon.css — radius ≤ 4px (999px capsules exempt),
 *      no box shadows, no gradients, colors only via the token block.
 *   3. Copy rules in design.md — no em dashes, and the artifact files it
 *      points agents at actually exist in web/public/.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const PUBLIC_DIR = join(__dirname, "..", "public");
const css = readFileSync(join(PUBLIC_DIR, "radon.css"), "utf8");
const md = readFileSync(join(PUBLIC_DIR, "design.md"), "utf8");
const example = readFileSync(join(PUBLIC_DIR, "design-example.html"), "utf8");

function classesFrom(text: string, pattern: RegExp): Set<string> {
  const found = new Set<string>();
  for (const match of text.matchAll(pattern)) found.add(match[1]);
  return found;
}

const cssClasses = classesFrom(css, /\.(rd-[a-z0-9-]+)/g);
const mdClasses = classesFrom(md, /`(rd-[a-z0-9-]+)`/g);

describe("vocabulary sync — design.md documents exactly the radon.css classes", () => {
  it("every class defined in radon.css is documented in design.md", () => {
    const undocumented = [...cssClasses].filter((c) => !mdClasses.has(c)).sort();
    expect(undocumented).toEqual([]);
  });

  it("every class documented in design.md exists in radon.css", () => {
    const ghosts = [...mdClasses].filter((c) => !cssClasses.has(c)).sort();
    expect(ghosts).toEqual([]);
  });

  it("the rendered example uses only documented classes", () => {
    const used = new Set<string>();
    for (const match of example.matchAll(/class="([^"]+)"/g)) {
      for (const cls of match[1].split(/\s+/)) {
        if (cls.startsWith("rd-")) used.add(cls);
      }
    }
    expect(used.size).toBeGreaterThan(10);
    const unknown = [...used].filter((c) => !cssClasses.has(c)).sort();
    expect(unknown).toEqual([]);
  });
});

describe("brand mechanics — radon.css", () => {
  it("border-radius never exceeds 4px, except the 999px badge capsule", () => {
    const radii = [...css.matchAll(/border-radius:\s*([0-9.]+)px/g)].map((m) =>
      Number(m[1]),
    );
    expect(radii.length).toBeGreaterThan(0);
    for (const r of radii) {
      expect(r <= 4 || r === 999, `border-radius ${r}px violates the 4px cap`).toBe(
        true,
      );
    }
  });

  it("declares no box shadows and no gradients", () => {
    expect(css).not.toMatch(/box-shadow/);
    expect(css).not.toMatch(/gradient/i);
    expect(css).not.toMatch(/backdrop-filter/);
  });

  it("uses color literals only inside the token blocks", () => {
    // Strip the :root and [data-theme="light"] token blocks, then assert the
    // remaining rules reference colors exclusively through var()/color-mix.
    const withoutTokens = css.replace(
      /(:root|\[data-theme="light"\])\s*\{[^}]*\}/g,
      "",
    );
    expect(withoutTokens).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(withoutTokens).not.toMatch(/rgba?\(/);
  });

  it("defines both themes over the same token set", () => {
    const block = (selector: string): Set<string> => {
      const m = css.match(
        new RegExp(`${selector.replace(/[[\]"]/g, "\\$&")}\\s*\\{([^}]*)\\}`),
      );
      expect(m, `missing ${selector} block`).toBeTruthy();
      return classesFrom(m![1], /(--[a-z-]+):/g);
    };
    const dark = block(":root");
    const light = block('[data-theme="light"]');
    // Fonts are theme-invariant and live only in :root.
    const darkColors = [...dark].filter((t) => !t.startsWith("--font")).sort();
    expect(darkColors).toEqual([...light].sort());
  });
});

describe("copy rules — design.md", () => {
  it("contains no em dashes", () => {
    expect(md.includes("\u2014")).toBe(false);
  });

  it("points at artifacts that exist and at the live stylesheet URL", () => {
    expect(md).toContain("https://app.radon.run/radon.css");
    expect(md).toContain("https://app.radon.run/design-example.html");
    expect(md).toContain("https://app.radon.run/brand/radon-monogram.svg");
    // The example must link the stylesheet by root-relative path so it renders
    // against the same origin it is served from.
    expect(example).toContain('href="/radon.css"');
  });
});
