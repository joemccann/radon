import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import { describe, expect, it, vi } from "vitest";

const filename = fileURLToPath(new URL("../../scripts/db/migrate.ts", import.meta.url));
const source = readFileSync(filename, "utf8");

async function migrate(manual?: string, failDrop = false, applied: number[] = []) {
  const statements: (string | { sql: string; args: number[] })[] = [];
  const db = { execute: vi.fn(async (statement: (typeof statements)[number]) => {
    statements.push(statement);
    if (statement === "DROP INDEX knowledge_idx" && failDrop) throw new Error("injected DDL failure");
    return { rows: statement === "SELECT version FROM schema_migrations" ? applied.map((version) => ({ version })) : [] };
  }) };
  const files: Record<string, string> = {
    "0089_manual.sql": "-- radon-migrate: manual\nDROP INDEX knowledge_idx;",
    "0090_auto.sql": "CREATE TABLE later (id INTEGER);",
  };
  const exit = vi.fn();
  const modules: Record<string, unknown> = {
    "@libsql/client": { createClient: () => db },
    "node:fs": {
      existsSync: () => true,
      readdirSync: () => Object.keys(files),
      readFileSync: (name: string) => files[path.basename(name)],
    },
    "node:path": path,
    "node:url": { fileURLToPath },
  };
  const code = ts.transpileModule(source.replaceAll("import.meta.url", JSON.stringify(new URL("../../scripts/db/migrate.ts", import.meta.url).href)), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  await runInNewContext(code, {
    exports: {},
    require: (name: string) => {
      if (!(name in modules)) throw new Error(`Unexpected dependency: ${name}`);
      return modules[name];
    },
    process: { env: { TURSO_DB_URL: "https://fake.invalid", TURSO_AUTH_TOKEN: "fixture", RADON_MIGRATE_MANUAL: manual }, exit },
    console: { log: vi.fn(), error: vi.fn(), warn: vi.fn() },
  }, { filename });
  return { statements, exit };
}

describe("TypeScript migration CLI manual DDL boundary", () => {
  it.each([undefined, "", "0", "true"])("leaves manual DDL pending for flag %s while applying later automatic work", async (flag) => {
    const { statements, exit } = await migrate(flag);
    expect(statements).not.toContain("DROP INDEX knowledge_idx");
    expect(statements).toContain("CREATE TABLE later (id INTEGER)");
    expect(statements.filter((value) => typeof value !== "string").map((value) => value.args)).toEqual([[90]]);
    expect(exit).not.toHaveBeenCalled();
  });

  it("executes opted-in DDL before recording its version, without a transaction", async () => {
    const { statements, exit } = await migrate("1");
    expect(statements.slice(2)).toEqual([
      "DROP INDEX knowledge_idx",
      { sql: expect.stringContaining("INSERT OR IGNORE INTO schema_migrations"), args: [89] },
      "CREATE TABLE later (id INTEGER)",
      { sql: expect.stringContaining("INSERT OR IGNORE INTO schema_migrations"), args: [90] },
    ]);
    expect(exit).not.toHaveBeenCalled();
  });

  it("does not record a failed manual migration or advance to later migrations", async () => {
    const { statements, exit } = await migrate("1", true);
    expect(statements.slice(2)).toEqual(["DROP INDEX knowledge_idx"]);
    expect(exit).toHaveBeenCalledExactlyOnceWith(1);
  });

  it("does not replay an already recorded manual migration", async () => {
    const { statements, exit } = await migrate("1", false, [89]);
    expect(statements).not.toContain("DROP INDEX knowledge_idx");
    expect(statements).toContain("CREATE TABLE later (id INTEGER)");
    expect(exit).not.toHaveBeenCalled();
  });
});
