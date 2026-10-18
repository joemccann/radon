import { describe, it, expect } from "vitest";
import { readFileSync, existsSync, readdirSync } from "node:fs";
import path from "node:path";
import { classifyRateTier } from "@/lib/demo/rateTier";

// DS-2026-10-18-03: /api/newsfeed/explain shipped as a fourth LLM route and was
// never added to AI_PATHS, so the middleware classifier charged it the generic
// mutation budget while its own route-keyed durable guard was an INDEPENDENT
// bucket. A demo user therefore got the documented shared daily AI allowance
// once per LLM route instead of once in total, against production provider
// keys. The list is not hand-maintained here: this walks the real import graph
// so the next LLM route cannot ship unmetered.

const WEB_ROOT = path.resolve(__dirname, "..");
const LLM_PROVIDER = path.join("lib", "llm", "provider.ts");

function resolveAlias(spec: string): string | null {
  const base = path.join(WEB_ROOT, spec.slice(2));
  for (const candidate of [`${base}.ts`, `${base}.tsx`, path.join(base, "index.ts")]) {
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

function importedAliases(file: string): string[] {
  const source = readFileSync(file, "utf-8");
  return [...source.matchAll(/(?:from|import)\s+"(@\/[^"]+)"/g)].map((m) => m[1]);
}

function reachesLlmProvider(entry: string): boolean {
  const seen = new Set<string>();
  const stack = [entry];
  while (stack.length > 0) {
    const file = stack.pop()!;
    if (seen.has(file)) continue;
    seen.add(file);
    if (path.relative(WEB_ROOT, file) === LLM_PROVIDER) return true;
    for (const alias of importedAliases(file)) {
      const resolved = resolveAlias(alias);
      if (resolved) stack.push(resolved);
    }
  }
  return false;
}

function collectRoutes(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) found.push(...collectRoutes(full));
    else if (entry.name === "route.ts") found.push(path.relative(WEB_ROOT, full));
  }
  return found;
}

describe("demo AI budget covers every LLM route", () => {
  const routes = collectRoutes(path.join(WEB_ROOT, "app", "api")).sort();

  it("finds the route files to check", () => {
    expect(routes.length).toBeGreaterThan(50);
  });

  it("every route that can reach the LLM provider is tier D", () => {
    const unmetered: string[] = [];
    for (const relative of routes) {
      if (!reachesLlmProvider(path.join(WEB_ROOT, relative))) continue;
      const pathname = `/${relative.replace(/^app\//, "").replace(/\/route\.ts$/, "")}`;
      if (classifyRateTier("POST", pathname) !== "D") unmetered.push(pathname);
    }
    expect(unmetered).toEqual([]);
  });

  it("the known LLM routes share one per-user budget key", () => {
    for (const pathname of [
      "/api/assistant",
      "/api/newsfeed/explain",
      "/api/newsfeed/share",
      "/api/ticker/seasonality",
    ]) {
      expect(classifyRateTier("POST", pathname)).toBe("D");
    }
  });
});
