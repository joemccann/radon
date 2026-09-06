/**
 * Clear corners are capped at 10px (approved direction A, 2026-09-05).
 *
 * The selected Clear design supersedes the old 4px visual ceiling. Keep the
 * restrained 6/8/10px scale bounded rather than removing the design contract.
 *
 * Three shapes are exempt because they are not panel corners:
 *   999px  - badge / pill capsules
 *   50% (any %) - circles: dots, avatars, scan rings
 *   var(--radius*) - the radius tokens themselves, capped at their definition
 *
 * A fourth escape hatch exists for shapes that are affordances rather than
 * panels, mirroring how `sortable-table-contract` uses `data-sortable-exempt`:
 * annotate the declaration with `/* radius-exempt: <reason> *\/` on the same
 * line or the line above, using one of ALLOWED_EXEMPTIONS. Anything else still
 * fails, so the hatch cannot become a silent bypass.
 *
 * Scope: `app/globals.css` plus inline styles in `components/`. Both longhand
 * (`border-top-left-radius`) and shorthand are covered, since longhand is the
 * usual way an oversized corner slips past review.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const MAX_PX = 10;
/** Reasons a non-panel shape may exceed the cap. Keep this list short. */
const ALLOWED_EXEMPTIONS = new Set(["sheet-grab-edge"]);
const EXEMPT_MARKER = /radius-exempt:\s*([a-z-]+)/i;
const CAPSULE_PX = 999;
const CSS_KEYWORDS = new Set(["inherit", "initial", "revert", "unset"]);

const WEB_ROOT = join(__dirname, "..");
const GLOBALS_CSS = join(WEB_ROOT, "app/globals.css");
const COMPONENTS = join(WEB_ROOT, "components");

/** `border-radius: X` / `border-top-left-radius: X` in a stylesheet. */
const CSS_RADIUS = /border-(?:radius|(?:top|bottom)-(?:left|right)-radius)\s*:\s*([^;{}]+)/gi;
/** `borderRadius: X` / `borderTopLeftRadius: X` in a JSX inline style object. */
const JSX_RADIUS = /border(?:Radius|(?:Top|Bottom)(?:Left|Right)Radius)\s*:\s*("[^"]*"|'[^']*'|[\d.]+)/g;

function walk(dir: string): string[] {
  const out: string[] = [];
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (p.endsWith(".tsx") || p.endsWith(".css")) out.push(p);
  }
  return out;
}

/** Blank out CSS comment bodies, preserving newlines so line numbers hold. */
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "));
}

/**
 * Returns the tokens in one radius value that break the cap.
 * A value may carry up to four corners plus an elliptical `/` second axis.
 */
function offendingTokens(rawValue: string): string[] {
  const value = rawValue.replace(/!important/gi, "").replace(/^["']|["']$/g, "").trim();
  if (!value) return [];

  const bad: string[] = [];
  for (const token of value.split(/[\s/]+/).filter(Boolean)) {
    if (/^var\(--radius[\w-]*\)$/.test(token)) continue;
    if (token.endsWith("%")) continue;
    if (CSS_KEYWORDS.has(token.toLowerCase())) continue;

    const px = /^(\d+(?:\.\d+)?)(?:px)?$/.exec(token);
    if (!px) {
      bad.push(`${token} (not a px value, a %, or var(--radius*))`);
      continue;
    }
    const n = Number(px[1]);
    if (n <= MAX_PX || n === CAPSULE_PX) continue;
    bad.push(token);
  }
  return bad;
}

/** An exemption marker on this line or the one above it, if it names a reason
 *  the contract actually allows. Read from the RAW source: `stripComments`
 *  blanks comment bodies, so the marker is only visible before that pass. */
function exemptionFor(rawLines: string[], index: number): string | null {
  for (const candidate of [rawLines[index], rawLines[index - 1]]) {
    const reason = candidate ? EXEMPT_MARKER.exec(candidate)?.[1] : undefined;
    if (reason && ALLOWED_EXEMPTIONS.has(reason)) return reason;
  }
  return null;
}

function collect(file: string, pattern: RegExp, stripped: boolean): string[] {
  const raw = readFileSync(file, "utf8");
  const rawLines = raw.split("\n");
  const src = stripped ? stripComments(raw) : raw;
  const rel = relative(WEB_ROOT, file);
  const violations: string[] = [];

  src.split("\n").forEach((line, i) => {
    if (exemptionFor(rawLines, i)) return;
    for (const match of line.matchAll(pattern)) {
      for (const token of offendingTokens(match[1])) {
        violations.push(`${rel}:${i + 1}: radius ${token} exceeds the Clear ${MAX_PX}px cap (allowed: <= ${MAX_PX}px, 999px, %, var(--radius*))`);
      }
    }
  });
  return violations;
}

describe("radius contract", () => {
  it("bounds corners across global styles, component stylesheets and inline styles", () => {
    const violations = [
      ...collect(GLOBALS_CSS, CSS_RADIUS, true),
      ...collect(join(WEB_ROOT, "app/clear.css"), CSS_RADIUS, true),
      ...walk(COMPONENTS).flatMap((f) => collect(f, f.endsWith(".css") ? CSS_RADIUS : JSX_RADIUS, f.endsWith(".css"))),
    ];

    expect(violations).toEqual([]);
  });
});
