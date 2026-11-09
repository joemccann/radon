/**
 * @vitest-environment node
 *
 * PERF-049: RegimePanel was a static import in WorkspaceSections, so every
 * non-regime workspace route (orders, scanner, journal, watchlist) paid its
 * eager graph (40 tab panels, d3 charts): 138 modules / 892592 source bytes
 * measured 2026-11-08. Dashboard, portfolio and performance already sit
 * behind next/dynamic on the shell; regime must too.
 */
import { readFileSync, existsSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = join(import.meta.dirname, "..");
const source = (path: string) => readFileSync(join(WEB, path), "utf8");

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

describe("PERF-049 regime panel chunk budget", () => {
  it("keeps RegimePanel out of the eager workspace-sections import graph", () => {
    const specs = eagerImportSpecifiers(source("components/WorkspaceSections.tsx"));
    expect(specs.has("InstrumentSkeleton"), "parser must see the sections file's real imports").toBe(true);
    expect([...specs]).not.toContain("RegimePanel");
    expect([...specs]).not.toContain("RegimePanel.tsx");
  });

  it("pays zero source bytes of the regime graph on the eager workspace-sections path", () => {
    const full = walkEager("components/WorkspaceSections.tsx");
    const withoutRegime = walkEager("components/WorkspaceSections.tsx", new Set(["RegimePanel.tsx"]));
    const pulled = full.filter((f) => !withoutRegime.includes(f));
    // Before: 138 modules / 892592 source bytes (2026-11-08 import-graph walk).
    expect(pulled.length, "static RegimePanel import must not pull regime modules into the workspace-sections chunk").toBe(0);
    expect(bytesOf(pulled)).toBe(0);
  });
});
