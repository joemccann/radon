/**
 * @vitest-environment node
 *
 * PERF-071: WorkspaceSections statically imported every cold section panel, so
 * orders, journal, scanner, flow and ticker pages paid their eager graphs
 * (admin, research workbench, options workspace, AI infrastructure, CTA,
 * profile, preferences, watchlist, alerts): 82 modules / 698271 source bytes
 * measured 2026-11-26. Regime already sits behind next/dynamic; these panels
 * render only for their own section and must too.
 */
import { readFileSync, existsSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = join(import.meta.dirname, "..");
const source = (path: string) => readFileSync(join(WEB, path), "utf8");

const COLD_SECTION_FILES = [
  "AdminWorkspace.tsx",
  "ResearchWorkbench.tsx",
  "OptionsWorkspacePanel.tsx",
  "AiInfrastructurePanel.tsx",
  "CtaPage.tsx",
  "ProfileContent.tsx",
  "PreferencesSection.tsx",
  "WatchlistContent.tsx",
  "AlertsPanel.tsx",
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

describe("PERF-071 cold section chunk budget", () => {
  it("keeps cold section panels out of the eager workspace-sections import graph", () => {
    const specs = eagerImportSpecifiers(source("components/WorkspaceSections.tsx"));
    expect(specs.has("InstrumentSkeleton"), "parser must see the sections file's real imports").toBe(true);
    expect(specs.has("TickerWorkspace"), "the ticker cockpit stays on the eager path").toBe(true);
    for (const file of COLD_SECTION_FILES) {
      const name = file.replace(/\.tsx$/, "");
      expect([...specs], `${file} must not be a static import`).not.toContain(name);
      expect([...specs]).not.toContain(file);
    }
  });

  it("pays zero source bytes of cold section panels on the eager workspace-sections path", () => {
    const full = walkEager("components/WorkspaceSections.tsx");
    const withoutCold = walkEager("components/WorkspaceSections.tsx", new Set(COLD_SECTION_FILES));
    const pulled = full.filter((f) => !withoutCold.includes(f));
    // Before: 82 modules / 698271 source bytes (2026-11-26 import-graph walk).
    expect(
      pulled.length,
      "static cold-section imports must not pull admin, research, options, AI, CTA, profile, preferences, watchlist or alerts into every workspace chunk",
    ).toBe(0);
    expect(bytesOf(pulled)).toBe(0);
  });
});
