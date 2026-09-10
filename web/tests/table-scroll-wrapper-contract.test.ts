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

import { mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
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

const TAG_HEAD = /<(\/?)([A-Za-z][\w.]*)\b/y;

type Tag = { close: boolean; name: string; attrs: string; selfClose: boolean; index: number };

/** Every JSX tag in `src`, `<` through its matching `>`, in document order.
 *
 * T-175 — this was one regex that modelled attribute brace nesting with three
 * hand-written levels of alternation, so a tag carrying a fourth
 * (`style={{ a: { b: { c: 1 } } }}`) did not match AT ALL. An unmatched opening
 * tag never reached the ancestor stack, so a correctly wrapped <table> beneath
 * it was reported unwrapped — a phantom violation; the same brace on a <table>
 * dropped that table out of the sweep entirely, hiding a real one. Since the
 * assertion below is an EQUALITY, a parser blind spot reds this file in both
 * directions. Depth is now COUNTED, so it holds at any nesting.
 *
 * A tag whose attributes carry JSX (`components={{ table: () => <div…> }}` in
 * MarkdownRenderer) is still descended into: the old regex only reached those
 * nested tags by ACCIDENT — it failed to match the outer tag, so its scan fell
 * through to them. Counting braces correctly consumes the outer tag in one
 * bite, so the embedded subtree is re-scanned explicitly, after the tag that
 * owns it, or that <table> would have silently left the sweep.
 *
 * Quotes are skipped as spans only when they close on the same line (or are
 * backticks), which pairs real attribute strings without an apostrophe in JSX
 * prose swallowing the rest of the file.
 *
 * Cost: one forward pass per region, no backtracking. Measured over the
 * ~271-file / 2.15 MB sweep across three runs — 91-564 ms for the whole file,
 * slowest single test 310 ms — against vitest's 5000 ms default. A 16x margin,
 * so no `vi.setConfig` bump is warranted the way T-161 needed one.
 */
function* tags(src: string, offset = 0): Generator<Tag> {
  for (let lt = src.indexOf("<"); lt >= 0; lt = src.indexOf("<", lt + 1)) {
    TAG_HEAD.lastIndex = lt;
    const head = TAG_HEAD.exec(src);
    if (!head) continue;
    const attrStart = lt + head[0].length;
    const nested: Array<[number, number]> = [];
    let depth = 0;
    let braceStart = -1;
    let end = -1;
    for (let i = attrStart; i < src.length; i++) {
      const c = src[i];
      if (c === '"' || c === "'" || c === "`") {
        const shut = src.indexOf(c, i + 1);
        if (shut < 0) break;
        if (c === "`" || !src.slice(i + 1, shut).includes("\n")) i = shut;
      } else if (c === "{") {
        if (depth === 0) braceStart = i + 1;
        depth++;
      } else if (c === "}") {
        depth--;
        if (depth === 0 && braceStart >= 0) nested.push([braceStart, i]);
      } else if (c === ">" && depth <= 0) {
        end = i;
        break;
      }
    }
    if (end < 0) continue;
    const raw = src.slice(attrStart, end);
    yield {
      close: Boolean(head[1]),
      name: head[2],
      attrs: raw.replace(/\/\s*$/, ""),
      selfClose: /\/\s*$/.test(raw),
      index: offset + lt,
    };
    for (const [from, to] of nested) yield* tags(src.slice(from, to), offset + from);
    lt = end;
  }
}

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
  const stack: Element[] = [];
  const tables: TableSite[] = [];
  const usages = new Map<string, boolean[]>();
  const scrolls = (el: Element, moduleClasses: Set<string>) =>
    el.tokens.some((t) => GLOBAL_OVERFLOW_CLASSES.has(t) || moduleClasses.has(t))
    || /overflowX\s*:/.test(el.attrs);

  return (moduleClasses: Set<string>) => {
    stack.length = 0;
    tables.length = 0;
    usages.clear();
    for (const { close, name, attrs, selfClose, index } of tags(src)) {
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
          line: src.slice(0, index).split("\n").length,
          wrapped: stack.some((a) => scrolls(a, moduleClasses)),
          owner: ownerAt(src, index),
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

  /* T-175. The sweep above is only as trustworthy as the tag parser under it,
   * and a parser blind spot is invisible from the repo scan alone: it moves
   * tables OUT of the sweep as readily as it invents violations. These pin the
   * three shapes that broke the fixed-depth brace alternation. All four fail
   * against it — the first two as phantom violations, the last two as tables
   * that vanish. */
  const DEEP = "style={{ a: { b: { c: 1 } } }}";
  const dir = mkdtempSync(join(tmpdir(), "table-scroll-t175-"));
  const sweep = (body: string) => {
    const p = join(dir, "Fixture.tsx");
    writeFileSync(p, `export function Fixture() {\n  return ${body};\n}\n`);
    return unwrappedTables(p).map((v) => v.slice(v.lastIndexOf(":") + 1));
  };

  it("resolves a wrapper whose attributes nest braces past three levels", () => {
    expect(sweep(`<div className="table-wrap" ${DEEP}><table /></div>`)).toEqual([]);
    expect(sweep(`<div className="table-wrap"><table ${DEEP} /></div>`)).toEqual([]);
  });

  it("still sees an unwrapped table that carries a deep brace itself", () => {
    expect(sweep(`<div><table ${DEEP} /></div>`)).toEqual(["Fixture"]);
  });

  it("descends into JSX embedded in a prop", () => {
    expect(sweep("<Md components={{ table: () => <table /> }} />")).toEqual(["Fixture"]);
    expect(
      sweep('<Md components={{ table: () => <div className="table-wrap"><table /></div> }} />'),
    ).toEqual([]);
  });
});
