#!/usr/bin/env -S bun run
// scripts/db/migrate.ts — apply pending Turso migrations to the cloud DB.
//
// Reads .sql files from scripts/db/migrations/ in lexicographic order
// (e.g. 0001_init.sql, 0002_add_foo.sql). Each filename's leading
// integer is its `version`. Skips versions already in schema_migrations.
//
// Run via:
//   cd web && bun run db:migrate
// or directly:
//   bun run scripts/db/migrate.ts
//
// The migration writes to the CLOUD DB (not a local replica), then
// every running replica picks up the new schema on its next sync.

import { createClient } from "@libsql/client";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const MIGRATIONS_DIR = path.resolve(__dirname, "migrations");

function readEnv(): { url: string; authToken: string } {
  // Prefer process.env (Bun loads .env automatically); fall back to
  // explicit dotenv reads if the user invoked us from a context that
  // didn't load it.
  const url = process.env.TURSO_DB_URL;
  const authToken = process.env.TURSO_AUTH_TOKEN;
  if (!url || !authToken) {
    console.error(
      "TURSO_DB_URL and TURSO_AUTH_TOKEN must be set. Add them to web/.env or root .env.",
    );
    process.exit(1);
  }
  return { url, authToken };
}

function listMigrations(): { version: number; name: string; absPath: string }[] {
  if (!fs.existsSync(MIGRATIONS_DIR)) {
    console.error(`No migrations directory at ${MIGRATIONS_DIR}`);
    process.exit(1);
  }
  return fs
    .readdirSync(MIGRATIONS_DIR)
    .filter((f) => /^\d+_.*\.sql$/.test(f))
    .sort()
    .map((name) => {
      const match = name.match(/^(\d+)_/)!;
      return {
        version: parseInt(match[1], 10),
        name,
        absPath: path.join(MIGRATIONS_DIR, name),
      };
    });
}

async function ensureMigrationsTable(db: ReturnType<typeof createClient>): Promise<void> {
  await db.execute(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version    INTEGER PRIMARY KEY,
      applied_at TEXT    NOT NULL
    )
  `);
}

async function appliedVersions(db: ReturnType<typeof createClient>): Promise<Set<number>> {
  const result = await db.execute("SELECT version FROM schema_migrations");
  const set = new Set<number>();
  for (const row of result.rows) {
    const v = row.version;
    if (typeof v === "number") set.add(v);
    else if (typeof v === "bigint") set.add(Number(v));
  }
  return set;
}

function splitStatements(sql: string): string[] {
  // Strip line comments, then split on `;` keeping non-empty statements.
  // libSQL doesn't support multi-statement execute() in one call, so
  // we run statements one at a time.
  const stripped = sql
    .split("\n")
    .map((line) => line.replace(/^\s*--.*$/, ""))
    .join("\n");
  return stripped
    .split(/;\s*$/m)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

async function main(): Promise<void> {
  const { url, authToken } = readEnv();
  const db = createClient({ url, authToken });

  await ensureMigrationsTable(db);
  const applied = await appliedVersions(db);
  const all = listMigrations();
  const pending = all.filter((m) => !applied.has(m.version));

  if (pending.length === 0) {
    console.log(`[migrate] nothing to apply — ${applied.size} migration(s) already at latest`);
    return;
  }

  console.log(`[migrate] applying ${pending.length} migration(s) → ${url}`);
  for (const m of pending) {
    console.log(`[migrate] → ${m.name}`);
    const sql = fs.readFileSync(m.absPath, "utf8");
    const statements = splitStatements(sql);
    for (const stmt of statements) {
      try {
        await db.execute(stmt);
      } catch (err) {
        console.error(`[migrate] FAILED on statement:\n${stmt.slice(0, 200)}\n`);
        throw err;
      }
    }
    // The migration file's INSERT INTO schema_migrations may already
    // record the version; if not, record it ourselves.
    await db.execute({
      sql: "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
      args: [m.version],
    });
  }

  console.log(`[migrate] done`);
}

main().catch((err) => {
  console.error("[migrate] fatal:", err);
  process.exit(1);
});
