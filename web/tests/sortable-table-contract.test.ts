/**
 * Product tables must be sortable.
 *
 * A file that contains `<table` must also contain one of:
 *   - SortTh / sortable-th / useSort
 *   - data-sortable-exempt="<reason>"
 *
 * Allowed exemption reasons:
 *   chain-layout — dual-sided options chain / strike ladder that must stay strike-ordered
 *   matrix       — row+column meaning (VIX COR base-rate table)
 *   markdown     — user/LLM HTML tables
 *   chrome       — checkbox/select-all or icon-only control tables that are not data grids
 *   kit-demo     — kit table that is not a product surface (prefer making the kit sortable)
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const ALLOWED = new Set(["chain-layout", "matrix", "markdown", "chrome", "kit-demo"]);
const ROOTS = [join(__dirname, "../components"), join(__dirname, "../app")];

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
    else if (p.endsWith(".tsx")) out.push(p);
  }
  return out;
}

describe("sortable table contract", () => {
  it("every product table uses SortTh/useSort or an explicit exemption", () => {
    const files = ROOTS.flatMap(walk);
    const webRoot = join(__dirname, "..");
    const violations: string[] = [];

    for (const file of files) {
      const src = readFileSync(file, "utf8");
      if (!/<table[\s>]/.test(src)) continue;

      const hasSort = /\bSortTh\b|\bsortable-th\b|\buseSort\b/.test(src);
      const reasons = [...src.matchAll(/data-sortable-exempt="([^"]+)"/g)].map((m) => m[1]);
      const invalid = reasons.filter((r) => !ALLOWED.has(r));
      const rel = relative(webRoot, file);

      if (invalid.length > 0) {
        violations.push(`${rel}: invalid data-sortable-exempt (${invalid.join(", ")})`);
        continue;
      }
      if (!hasSort && reasons.length === 0) {
        violations.push(`${rel}: <table> without SortTh/useSort or data-sortable-exempt`);
      }
    }

    expect(violations).toEqual([]);
  });
});
