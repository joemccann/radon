// libSQL client for the Radon Turso DB.
//
// All Next.js API routes and FastAPI Python schedulers read/write through
// this client. Embedded replicas were retired on 2026-05-20 after WAL
// conflicts between local Node readers and Python/direct-cloud writers.
// The safe default is direct-to-cloud; a replica requires explicit opt-in.
//
// One singleton per process. The schema is compiled to a no-op when
// TURSO_DB_URL is unset (tests can mock the module).

import { createClient, type Client } from "@libsql/client";
import path from "node:path";

let cached: Client | null = null;

function projectRoot(): string {
  // Resolve relative to the calling Next.js process. `process.cwd()` is
  // the `web/` dir under `npm run dev`; in `app-radon-run` Docker it's
  // `/app`. Either way, putting the replica next to `data/` matches the
  // repo layout and keeps the file out of git.
  return path.resolve(process.cwd(), "..", "data");
}

export function getDb(): Client {
  if (cached) return cached;

  const url = process.env.TURSO_DB_URL;
  const authToken = process.env.TURSO_AUTH_TOKEN;

  if (!url) {
    throw new Error(
      "getDb: TURSO_DB_URL is not set. Add it to web/.env. See plan §0.",
    );
  }

  const useReplica = (
    process.env.NODE_ENV !== "test" &&
    process.env.RADON_DB_USE_REPLICA === "1" &&
    process.env.RADON_DB_NO_REPLICA !== "1"
  );

  cached = createClient(
    useReplica
      ? {
          url: `file:${path.join(projectRoot(), "replica.db")}`,
          syncUrl: url,
          authToken,
          syncInterval: 60, // background pull every 60s; writes are pushed immediately
        }
      : { url, authToken },
  );

  return cached;
}

let cachedDemo: Client | null = null;

/**
 * libSQL client for the SEPARATE demo Turso DB (demo.radon.run).
 *
 * Reads `TURSO_DEMO_DB_URL` / `TURSO_DEMO_AUTH_TOKEN` — never the prod
 * `TURSO_DB_URL`. The whole point of the demo environment is physical data
 * isolation (plan §Isolation): the operator's real positions live in a
 * different database that is simply absent here, so a forgotten WHERE clause
 * can never leak them. Direct-to-cloud only; demo has no replica.
 *
 * Matches the env names already consumed by `scripts/db/demo_seed.py` and
 * `scripts/ci/check_demo_isolation.py`.
 */
export function getDemoDb(): Client {
  if (cachedDemo) return cachedDemo;

  const url = process.env.TURSO_DEMO_DB_URL;
  const authToken = process.env.TURSO_DEMO_AUTH_TOKEN;

  if (!url) {
    throw new Error(
      "getDemoDb: TURSO_DEMO_DB_URL is not set. This is the SEPARATE demo DB, " +
        "not the prod TURSO_DB_URL. See docs/demo-environment.md §Isolation.",
    );
  }

  cachedDemo = createClient({ url, authToken });
  return cachedDemo;
}

// Legacy convenience for explicit replica opt-in callers.
export async function syncDb(): Promise<void> {
  const db = getDb();
  if ("sync" in db && typeof (db as { sync?: () => Promise<unknown> }).sync === "function") {
    await (db as { sync: () => Promise<unknown> }).sync();
  }
}

// Test seam — drop the cached client between vitest tests.
export function __resetDbForTests(): void {
  cached = null;
  cachedDemo = null;
}

// Test seam — inject a libSQL client (typically in-memory) so route
// handlers can be tested hermetically.
export function __setDbForTests(client: Client): void {
  cached = client;
}

// Test seam — inject the demo libSQL client.
export function __setDemoDbForTests(client: Client): void {
  cachedDemo = client;
}
