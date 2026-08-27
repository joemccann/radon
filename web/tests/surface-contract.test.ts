/**
 * Surface contract for `app/globals.css`.
 *
 * Radon separates surfaces with 1px hairlines and background lightness steps,
 * not with shadows. A panel sits above another panel because it is a step
 * lighter and ringed by a hairline border, so depth stays legible at any zoom,
 * in light and dark, and on a projector. Soft consumer drop shadows blur that
 * hierarchy into a suggestion and read as marketing chrome on a terminal.
 *
 * Blur is worse than a style preference: `backdrop-filter` forces the
 * compositor to read the framebuffer back and re-blur it every frame the
 * content underneath changes. Behind live quotes, a streaming chain, or a
 * ticking blotter that is a per-frame readback on the hot path, so
 * glassmorphism is banned outright rather than budgeted.
 *
 * Two rules, both scanned over `app/globals.css`:
 *   1. Zero `backdrop-filter` / `-webkit-backdrop-filter` declarations.
 *      The fix is to delete the declaration and use `--bg-panel-raised`
 *      plus a `--border-dim` hairline.
 *   2. No box-shadow layer with a blur radius >= 8px. Explicitly allowed:
 *      - any `inset` layer (hairline rings, selection chrome, edge rules)
 *      - blur < 8px (covers `0 0 0 1px` rings and tight focus halos)
 *      - a NEGATIVE spread, which is how the sticky-column scroll cues are
 *        drawn: the negative spread pulls the shadow back under its own
 *        element so only a narrow directional cue escapes the edge, e.g.
 *        `-24px 0 60px -30px`. That is a scroll affordance, not depth.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const CSS_PATH = join(__dirname, "../app/globals.css");
const BLUR_LIMIT_PX = 8;

type Declaration = { prop: string; value: string; line: number; selector: string };

/** Blank out block comments, preserving newlines so line numbers stay true. */
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "));
}

/**
 * Collect declarations for the given properties, tracking the selector that
 * opened the current block and joining values that wrap across lines.
 */
function collectDeclarations(src: string, props: RegExp): Declaration[] {
  const lines = stripComments(src).split("\n");
  const out: Declaration[] = [];
  let selector = "(top level)";
  let pending: Declaration | null = null;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (pending) {
      pending.value += ` ${line}`;
      if (line.includes(";")) {
        pending.value = pending.value.slice(0, pending.value.indexOf(";"));
        out.push(pending);
        pending = null;
      }
      continue;
    }

    const brace = line.indexOf("{");
    if (brace !== -1) {
      const head = line.slice(0, brace).trim();
      if (head) selector = head;
    }

    const decl = line.match(props);
    if (!decl) continue;

    const rest = line.slice(line.indexOf(decl[0]) + decl[0].length);
    const entry: Declaration = {
      prop: decl[1],
      value: rest,
      line: i + 1,
      selector,
    };
    if (rest.includes(";")) {
      entry.value = rest.slice(0, rest.indexOf(";"));
      out.push(entry);
    } else {
      pending = entry;
    }
  }

  return out;
}

/** Split a shadow value on top-level commas, ignoring commas inside functions. */
function splitLayers(value: string): string[] {
  const layers: string[] = [];
  let depth = 0;
  let current = "";
  for (const ch of value) {
    if (ch === "(") depth++;
    else if (ch === ")") depth--;
    if (ch === "," && depth === 0) {
      layers.push(current);
      current = "";
      continue;
    }
    current += ch;
  }
  if (current.trim()) layers.push(current);
  return layers.map((l) => l.trim()).filter(Boolean);
}

/** Lengths of one shadow layer, in px, with functions and colours removed. */
function lengthsPx(layer: string): number[] {
  let bare = layer;
  // Drop nested functions (color-mix, var, rgba, oklch) so their numbers
  // cannot be mistaken for offsets.
  let prev: string;
  do {
    prev = bare;
    bare = bare.replace(/[a-z-]+\([^()]*\)/gi, " ");
  } while (bare !== prev);

  const out: number[] = [];
  for (const token of bare.trim().split(/\s+/)) {
    const m = token.match(/^(-?\d*\.?\d+)(px|rem|em)?$/);
    if (!m) continue;
    const n = Number(m[1]);
    out.push(m[2] === "rem" || m[2] === "em" ? n * 16 : n);
  }
  return out;
}

describe("surface contract (globals.css)", () => {
  const css = readFileSync(CSS_PATH, "utf8");

  it("uses zero backdrop-filter declarations (no glassmorphism)", () => {
    const decls = collectDeclarations(css, /((?:-webkit-)?backdrop-filter)\s*:/i);

    const violations = decls.map(
      (d) =>
        `globals.css:${d.line} — ${d.selector} { ${d.prop}:${d.value.trimEnd()} } ` +
        `(delete it; separate the surface with --bg-panel-raised + a --border-dim hairline)`,
    );

    expect(violations).toEqual([]);
  });

  it("uses no soft drop shadows (blur >= 8px on a non-inset layer)", () => {
    const decls = collectDeclarations(css, /(box-shadow)\s*:/i);
    const violations: string[] = [];

    for (const d of decls) {
      for (const layer of splitLayers(d.value)) {
        if (/^none$/i.test(layer)) continue;
        // Allowed: inset layers. Hairline rings, selection chrome and edge
        // rules are drawn inset and add no ambient depth.
        if (/\binset\b/i.test(layer)) continue;

        const [, , blur, spread] = lengthsPx(layer);
        // Allowed: tight halos and `0 0 0 1px` hairline rings.
        if (blur === undefined || blur < BLUR_LIMIT_PX) continue;
        // Allowed: sticky-column scroll cues. A negative spread contracts the
        // shadow back under its own element, so only a narrow directional cue
        // escapes the edge (e.g. `-24px 0 60px -30px`). Scroll affordance,
        // not surface depth.
        if (spread !== undefined && spread < 0) continue;

        violations.push(
          `globals.css:${d.line} — ${d.selector} { box-shadow: ${layer} } ` +
            `blur ${blur}px >= ${BLUR_LIMIT_PX}px with no inset and no negative spread`,
        );
      }
    }

    expect(violations).toEqual([]);
  });
});
