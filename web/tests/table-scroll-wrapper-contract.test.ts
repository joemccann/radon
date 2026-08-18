/**
 * A table's scroll wrapper must be a class that actually exists.
 *
 * Bug (2026-08-18 screenshot): the vol cone scanner table was unreadable on
 * mobile — content bisected by the panel border, page scrolling sideways.
 * `VolConePanel` (and `LeapScanner`, `GarchConvergenceScanner`) wrapped their
 * 9-column tables in `<div className="table-scroll">`, a class defined in NO
 * stylesheet. The wrapper was an unstyled div, so on a 390px viewport the
 * table overflowed the document instead of scrolling inside its container —
 * violating the "wide content scrolls in its own overflow-x container, the
 * page never scrolls horizontally" rule.
 *
 * Contract: every `*table-scroll*` / `*table-wrap*` class token used in a
 * component's className must have a stylesheet definition that sets
 * `overflow-x`. A typo'd or phantom wrapper class fails here instead of
 * shipping as an invisible no-op.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const WEB_ROOT = join(__dirname, "..");
const TSX_ROOTS = [join(WEB_ROOT, "components"), join(WEB_ROOT, "app")];

function walk(dir: string, ext: string): string[] {
  const out: string[] = [];
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p, ext));
    else if (p.endsWith(ext)) out.push(p);
  }
  return out;
}

/** Class-token lists for every className attribute that names a table wrapper.
 *  Template-literal interpolations (`${...}`) split like whitespace, so
 *  `table-wrap${density}` yields the checkable "table-wrap" token. */
function wrapperElementsIn(src: string): string[][] {
  const out: string[][] = [];
  for (const attr of src.matchAll(/className=\{?["'`]([^"'`]+)["'`]/g)) {
    const tokens = attr[1].split(/\s+|\$\{[^}]*\}|\$\{/).filter(Boolean);
    if (tokens.some((t) => /table-(scroll|wrap)/.test(t))) out.push(tokens);
  }
  return out;
}

describe("table scroll wrapper contract", () => {
  it("every table-scroll/table-wrap class used in TSX has an overflow-x stylesheet definition", () => {
    const stylesheets = [
      join(WEB_ROOT, "app", "globals.css"),
      ...walk(join(WEB_ROOT, "components"), ".css"),
      ...walk(join(WEB_ROOT, "app"), ".css"),
    ];
    const css = [...new Set(stylesheets)].map((p) => readFileSync(p, "utf8")).join("\n");

    const definesWithOverflow = (token: string): boolean => {
      // Any rule block whose selector names the class and whose body sets overflow-x.
      const rule = new RegExp(`\\.${token}[^{}]*\\{[^}]*overflow-x`, "s");
      return rule.test(css);
    };

    const violations: string[] = [];
    for (const file of TSX_ROOTS.flatMap((r) => walk(r, ".tsx"))) {
      const src = readFileSync(file, "utf8");
      for (const tokens of wrapperElementsIn(src)) {
        if (!tokens.some(definesWithOverflow)) {
          violations.push(
            `${relative(WEB_ROOT, file)}: wrapper "${tokens.join(" ")}" — no class carries an overflow-x rule`,
          );
        }
      }
    }

    expect(violations).toEqual([]);
  });
});
