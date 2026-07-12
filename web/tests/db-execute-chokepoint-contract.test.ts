/**
 * Contract: every Next.js API route that touches Turso must go through
 * dbExecute (bounded + self-healing). Bare getDb().execute hangs to undici's
 * ~300s body timeout on a stale keep-alive socket and never heals the pool.
 *
 * 2026-07-02 incident: routes that skipped the chokepoint turned a pool wedge
 * into multi-minute hangs while routes on dbExecute failed fast + recovered.
 *
 * Allowlist is empty for app/api — helpers under lib/ may call getDb only via
 * dbExecute.ts itself.
 */
import { describe, it, expect } from "vitest";
import { readdir, readFile } from "fs/promises";
import { join } from "path";

const WEB_ROOT = join(__dirname, "..");
const API_ROOT = join(WEB_ROOT, "app", "api");
const LIB_ROOT = join(WEB_ROOT, "lib");

const BARE_EXECUTE = /getDb\s*\(\s*\)\s*\.\s*execute\s*\(/;

async function collectTsFiles(dir: string): Promise<string[]> {
  const out: string[] = [];
  const entries = await readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...(await collectTsFiles(path)));
    } else if (entry.isFile() && (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx"))) {
      out.push(path);
    }
  }
  return out;
}

function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

describe("dbExecute chokepoint — no bare getDb().execute in API routes", () => {
  it("app/api/** never calls getDb().execute directly", async () => {
    const files = await collectTsFiles(API_ROOT);
    const offenders: string[] = [];
    for (const file of files) {
      const src = stripComments(await readFile(file, "utf8"));
      if (BARE_EXECUTE.test(src)) {
        offenders.push(file.slice(WEB_ROOT.length + 1));
      }
    }
    expect(offenders).toEqual([]);
  });

  it("lib/ only uses bare getDb().execute inside dbExecute.ts", async () => {
    const files = await collectTsFiles(LIB_ROOT);
    const offenders: string[] = [];
    for (const file of files) {
      if (file.endsWith(`${join("lib", "dbExecute.ts")}`) || file.endsWith("/lib/dbExecute.ts")) {
        continue;
      }
      // db.ts documents the pattern; must not call it at runtime either.
      const src = stripComments(await readFile(file, "utf8"));
      // Comments already stripped — only live calls fail.
      if (BARE_EXECUTE.test(src)) {
        offenders.push(file.slice(WEB_ROOT.length + 1));
      }
    }
    expect(offenders).toEqual([]);
  });
});
