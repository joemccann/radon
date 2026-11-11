/**
 * @vitest-environment node
 *
 * PERF-050: RegimePanel statically imported every tab panel, so opening any
 * /regime/* tab paid the unused charts (d3, visx, per-indicator libs):
 * 144 modules / 842952 source bytes measured 2026-11-10. Dashboard and
 * workspace-sections already sit behind next/dynamic (PERF-042 / PERF-049);
 * unused regime tabs must too.
 */
import { readFileSync, existsSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = join(import.meta.dirname, "..");
const source = (path: string) => readFileSync(join(WEB, path), "utf8");

const TAB_PANEL_FILES = [
  "AiInfrastructurePanel.tsx",
  "AsiaExportLeadPanel.tsx",
  "AtsVenueSharePanel.tsx",
  "BacktestPanel.tsx",
  "BpiPanel.tsx",
  "BreadthPanel.tsx",
  "CalmStreakPanel.tsx",
  "CorPanel.tsx",
  "CreditSpreadPanel.tsx",
  "CreditVixPanel.tsx",
  "DispersionPanel.tsx",
  "DivYieldPanel.tsx",
  "EquiblesCotPanel.tsx",
  "EquiblesShortCrowdingPanel.tsx",
  "GammaRotationPanel.tsx",
  "GexPanel.tsx",
  "HhLevPanel.tsx",
  "HighLowLinePanel.tsx",
  "HyAdPanel.tsx",
  "IeiHygPanel.tsx",
  "ImbalancePanel.tsx",
  "IvRankPanel.tsx",
  "IvSpreadPanel.tsx",
  "MaRatioPanel.tsx",
  "MarginDebtPanel.tsx",
  "MsrPanel.tsx",
  "PanicIndexPanel.tsx",
  "QuantPanel.tsx",
  "RecordHighBreadthPanel.tsx",
  "RsiOversoldPanel.tsx",
  "Skew2dPanel.tsx",
  "SkewPanel.tsx",
  "StraddlePanel.tsx",
  "StreaksPanel.tsx",
  "TrinPanel.tsx",
  "VcgPanel.tsx",
  "VixCorPanel.tsx",
  "VixModelSpreadPanel.tsx",
  "VixPcPanel.tsx",
  "VixTsPanel.tsx",
  "YieldCurvePanel.tsx",
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

describe("PERF-050 regime tab panel chunk budget", () => {
  it("keeps unused tab panels out of the eager RegimePanel import graph", () => {
    const specs = eagerImportSpecifiers(source("components/RegimePanel.tsx"));
    expect(specs.has("CriHistoryChart"), "parser must see the CRI chart import").toBe(true);
    for (const file of TAB_PANEL_FILES) {
      const name = file.replace(/\.tsx$/, "");
      expect([...specs], `${file} must not be a static import`).not.toContain(name);
      expect([...specs]).not.toContain(file);
    }
  });

  it("pays zero source bytes of unused tab panels on the eager RegimePanel path", () => {
    const skip = new Set<string>(TAB_PANEL_FILES);
    const full = walkEager("components/RegimePanel.tsx");
    const withoutTabs = walkEager("components/RegimePanel.tsx", skip);
    const pulled = full.filter((f) => !withoutTabs.includes(f));
    // Before: 144 modules / 842952 source bytes (2026-11-10 import-graph walk).
    expect(
      pulled.length,
      "static tab-panel imports must not pull unused charts into every /regime/* chunk",
    ).toBe(0);
    expect(bytesOf(pulled)).toBe(0);
  });
});
