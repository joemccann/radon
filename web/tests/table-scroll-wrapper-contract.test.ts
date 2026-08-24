/**
 * Every rendered <table> must sit inside a container that actually scrolls.
 *
 * Bug (2026-08-18 screenshot): the vol cone scanner table was unreadable on
 * mobile — content bisected by the panel border, page scrolling sideways.
 * `VolConePanel` (and `LeapScanner`, `GarchConvergenceScanner`) wrapped their
 * 9-column tables in `<div className="table-scroll">`, a class defined in NO
 * stylesheet. The wrapper was an unstyled div, so on a 390px viewport the
 * table overflowed the document instead of scrolling inside its container.
 *
 * T-113 — this contract used to run the wrong way round. It collected a
 * className only when a token already matched /table-(scroll|wrap)/ and
 * SKIPPED everything else, so renaming both `VolConePanel` wrappers to an
 * undefined class reintroduced the exact bug with the file reporting 1 passed.
 * A named wrapper was required to be styled; an unnamed one was not required
 * to exist.
 *
 * Inverted: enumerate every `<table>` in the TSX tree and require an ancestor
 * that establishes horizontal overflow — a class with an `overflow-x` rule in
 * `globals.css` or in the component's own CSS module, or an inline
 * `overflowX`. A table rendered by a helper component is resolved through that
 * component's call sites in the same file. The opt-out is explicit and
 * reasoned: `data-overflow-exempt="<reason>"` on the table.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const WEB_ROOT = join(__dirname, "..");
const TSX_ROOTS = [join(WEB_ROOT, "components"), join(WEB_ROOT, "app")];

/** Sites the sweep found unwrapped when it was inverted (T-121).
 *
 * Each is a real horizontal-overflow defect of the 2026-08-18 class, not a
 * measurement artefact: none of `.ratings-changes`, `.seasonality-detail`,
 * `.data-table`, `.section-body` carries an `overflow-x` rule anywhere. They
 * are pinned here rather than silently skipped, and the assertion is an
 * EQUALITY — fixing one reds this file until its entry is removed, and a
 * seventh unwrapped table reds it immediately.
 */
const KNOWN_UNWRAPPED_T121 = [
  "components/WorkspaceSections.tsx:OrdersSections",
  "components/WorkspaceSections.tsx:HistoricalTradesSection",
  "components/equibles-cot/EquiblesCotPanel.tsx:CotBoardTable",
  "components/flow-analysis/DailyDarkPoolHistory.tsx:DailyTable",
  "components/ticker-detail/RatingsTab.tsx:RatingsChangesTable",
  "components/ticker-detail/SeasonalityTab.tsx:SeasonalityDetailTable",
];

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

/** Class names named by any rule whose body sets `overflow-x`.
 *
 * Precomputed in ONE pass per stylesheet. The first draft built a fresh
 * RegExp against the whole ~1 MB `globals.css` for every class token of every
 * ancestor of every tag, which put the sweep at 4.5-6 s — straddling vitest's
 * 5 s default and flaking 1-in-2. Measurement that decides the verdict by how
 * loaded the runner is is not a contract.
 */
function overflowClasses(css: string): Set<string> {
  const names = new Set<string>();
  for (const rule of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!rule[2].includes("overflow-x")) continue;
    for (const cls of rule[1].matchAll(/\.([-_a-zA-Z][-_\w]*)/g)) names.add(cls[1]);
  }
  return names;
}

const GLOBAL_OVERFLOW_CLASSES = overflowClasses(
  [
    join(WEB_ROOT, "app", "globals.css"),
    ...walk(join(WEB_ROOT, "components"), ".css"),
    ...walk(join(WEB_ROOT, "app"), ".css"),
  ]
    .filter((p) => !p.endsWith(".module.css"))
    .map((p) => readFileSync(p, "utf8"))
    .join("\n"),
);

/** HTML containers a table can be nested in. TS generics are not tags. */
const HTML_TAGS = new Set([
  "div", "section", "main", "article", "aside", "form", "nav", "header",
  "footer", "figure", "li", "span", "p", "label", "td", "th", "details",
]);

const TAG =
  /<(\/?)([A-Za-z][\w.]*)\b((?:[^>"'`{}]|"[^"]*"|'[^']*'|`[^`]*`|\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})*?)(\/?)>/g;

type Element = { name: string; attrs: string; tokens: string[] };
type TableSite = { line: number; wrapped: boolean; owner: string | null; exempt: string | null };

function classTokens(attrs: string, moduleAlias: string | null): string[] {
  const literal = attrs.match(/className=\{?["'`]([^"'`]*)["'`]/);
  if (literal) return literal[1].split(/\s+|\$\{[^}]*\}|\$\{/).filter(Boolean);
  if (!moduleAlias) return [];
  // `className={styles.tableWrap}` / `className={`${styles.a} b`}` etc.
  const escaped = moduleAlias.replace(/[$]/g, "\\$&");
  return [...attrs.matchAll(new RegExp(`${escaped}\\.([\\w$]+)`, "g"))].map((m) => m[1]);
}

/** The nearest enclosing component declaration before `index`. */
function ownerAt(src: string, index: number): string | null {
  const decls = src
    .slice(0, index)
    .match(/(?:function\s+([A-Z]\w*)\s*\(|const\s+([A-Z]\w*)\s*(?::[^=\n]*)?=\s*(?:\([^)]*\)|\w+)\s*(?::[^=\n]*)?=>)/g);
  if (!decls?.length) return null;
  return (decls[decls.length - 1].match(/[A-Z]\w*/) ?? [null])[0];
}

function analyze(src: string, moduleAlias: string | null) {
  TAG.lastIndex = 0;
  const stack: Element[] = [];
  const tables: TableSite[] = [];
  const usages = new Map<string, boolean[]>();
  const scrolls = (el: Element, moduleClasses: Set<string>) =>
    el.tokens.some((t) => GLOBAL_OVERFLOW_CLASSES.has(t) || moduleClasses.has(t))
    || /overflowX\s*:/.test(el.attrs);

  return (moduleClasses: Set<string>) => {
    TAG.lastIndex = 0;
    stack.length = 0;
    tables.length = 0;
    usages.clear();
    for (let m = TAG.exec(src); m; m = TAG.exec(src)) {
      const [, close, name, attrs, selfClose] = m;
      const isComponent = /^[A-Z]/.test(name);
      if (!HTML_TAGS.has(name) && name !== "table" && !isComponent) continue;
      if (close) {
        for (let i = stack.length - 1; i >= 0; i--) {
          if (stack[i].name === name) {
            stack.length = i;
            break;
          }
        }
        continue;
      }
      const el: Element = { name, attrs, tokens: classTokens(attrs, moduleAlias) };
      if (name === "table") {
        const exempt = attrs.match(/data-overflow-exempt=["']([^"']+)["']/);
        tables.push({
          line: src.slice(0, m.index).split("\n").length,
          wrapped: stack.some((a) => scrolls(a, moduleClasses)),
          owner: ownerAt(src, m.index),
          exempt: exempt?.[1] ?? null,
        });
      }
      if (isComponent) {
        if (!usages.has(name)) usages.set(name, []);
        usages.get(name)!.push(stack.some((a) => scrolls(a, moduleClasses)));
      }
      if (selfClose) continue;
      stack.push(el);
    }
    return { tables, usages };
  };
}

export function unwrappedTables(file: string): string[] {
  const src = readFileSync(file, "utf8");
  const moduleImport = src.match(/import\s+(\w+)\s+from\s+["'](\.[^"']*\.module\.css)["']/);
  const moduleAlias = moduleImport?.[1] ?? null;
  let moduleCss = "";
  if (moduleImport) {
    try {
      moduleCss = readFileSync(resolve(dirname(file), moduleImport[2]), "utf8");
    } catch {
      moduleCss = "";
    }
  }
  const { tables, usages } = analyze(src, moduleAlias)(overflowClasses(moduleCss));

  const out: string[] = [];
  for (const table of tables) {
    if (table.wrapped || table.exempt) continue;
    const callSites = table.owner ? usages.get(table.owner) : undefined;
    // A helper component's table is wrapped if EVERY call site wraps it.
    if (callSites?.length && callSites.every(Boolean)) continue;
    out.push(`${relative(WEB_ROOT, file)}:${table.owner ?? `line ${table.line}`}`);
  }
  return out;
}

describe("table scroll wrapper contract", () => {
  const files = TSX_ROOTS.flatMap((root) => walk(root, ".tsx"));

  it("sees a non-trivial number of tables — an empty sweep would be vacuous", () => {
    const total = files.reduce((n, f) => n + (readFileSync(f, "utf8").match(/<table\b/g)?.length ?? 0), 0);
    expect(total).toBeGreaterThan(20);
  });

  it("every <table> has an ancestor that establishes horizontal overflow", () => {
    const violations = files.flatMap(unwrappedTables).sort();
    expect(violations).toEqual([...KNOWN_UNWRAPPED_T121].sort());
  });
});
