/**
 * @vitest-environment node
 *
 * PERF-072: AssetDeck statically imported every flyout deck, so the ticker
 * page's first paint (the book) paid chain, position, news, ratings,
 * seasonality, company, 13F and filing-forensics graphs: 47 modules /
 * 386658 source bytes measured 2026-11-26. The docked order ticket stays
 * eager. A deck loads only when that deck is open.
 */
import { readFileSync, existsSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = join(import.meta.dirname, "..");
const source = (path: string) => readFileSync(join(WEB, path), "utf8");

const DECK_TAB_FILES = [
  "OptionsChainTab.tsx",
  "PositionTab.tsx",
  "NewsTab.tsx",
  "RatingsTab.tsx",
  "SeasonalityTab.tsx",
  "CompanyTab.tsx",
  "SmartMoney13FPanel.tsx",
  "FilingForensicsDossier.tsx",
] as const;

function eagerImportSpecifiers(text: string): Set<string> {
  const specs = new Set<string>();
  const pattern = /^(?:import|export)\s+(?!type[\s{])[\s\S]*?\bfrom\s*["']([^"']+)["']/gm;
  for (const match of text.matchAll(pattern)) {
    const spec = match[1];
    const local = spec.startsWith("@/") || spec.startsWith(".");
    specs.add(local ? spec.split("/").pop()! : spec);
  }
  return specs;
}

const FROM_RE = /^(?:import|export)\s+(?!type[\s{])[\s\S]*?\bfrom\s*["']([^"']+)["']/gm;
const SIDE_RE = /^import\s+["']([^"']+)["']/gm;

function resolveLocal(fromFile: string, spec: string): string | null {
  let base: string;
  if (spec.startsWith("@/")) base = join(WEB, spec.slice(2));
  else if (spec.startsWith(".")) base = resolve(dirname(fromFile), spec);
  else return null;
  const candidates = [
    base,
    `${base}.tsx`,
    `${base}.ts`,
    `${base}.jsx`,
    `${base}.js`,
    `${base}.css`,
    `${base}.module.css`,
    join(base, "index.ts"),
    join(base, "index.tsx"),
  ];
  return candidates.find((c) => existsSync(c)) ?? null;
}

function walkEager(entryRel: string, skipNames: Set<string> = new Set()): string[] {
  const files: string[] = [];
  const seen = new Set<string>();
  const stack = [resolve(WEB, entryRel)];
  while (stack.length) {
    const file = stack.pop()!;
    if (seen.has(file)) continue;
    seen.add(file);
    const name = file.split("/").pop()!;
    if (skipNames.has(name)) continue;
    files.push(file);
    let text: string;
    try {
      text = readFileSync(file, "utf8");
    } catch {
      continue;
    }
    const specs = [...text.matchAll(FROM_RE), ...text.matchAll(SIDE_RE)].map((m) => m[1]);
    for (const spec of specs) {
      const resolved = resolveLocal(file, spec);
      if (resolved) stack.push(resolved);
    }
  }
  return files;
}

function bytesOf(files: string[]): number {
  return files.reduce((sum, f) => sum + statSync(f).size, 0);
}

describe("PERF-072 asset deck tab chunk budget", () => {
  it("keeps flyout decks out of the eager AssetDeck import graph", () => {
    const specs = eagerImportSpecifiers(source("components/ticker-detail/AssetDeck.tsx"));
    expect(specs.has("OrderTab"), "the docked order ticket stays a static import").toBe(true);
    for (const file of DECK_TAB_FILES) {
      const name = file.replace(/\.tsx$/, "");
      expect([...specs], `${file} must not be a static import`).not.toContain(name);
      expect([...specs]).not.toContain(file);
    }
  });

  it("pays zero source bytes of flyout decks on the eager AssetDeck path", () => {
    const full = walkEager("components/ticker-detail/AssetDeck.tsx");
    const withoutTabs = walkEager("components/ticker-detail/AssetDeck.tsx", new Set(DECK_TAB_FILES));
    const pulled = full.filter((f) => !withoutTabs.includes(f));
    // Before: 47 modules / 386658 source bytes (2026-11-26 import-graph walk).
    expect(
      pulled.length,
      "static deck imports must not pull chain, position, news, ratings, seasonality, company, 13F or filings into the book-first ticker chunk",
    ).toBe(0);
    expect(bytesOf(pulled)).toBe(0);
  });
});
