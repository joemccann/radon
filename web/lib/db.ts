// libSQL client + embedded replica for the Radon Turso DB.
//
// All Next.js API routes and FastAPI Python schedulers read/write through
// this client. Reads hit the local replica file (SQLite-fast, <100µs);
// writes go to the cloud DB and stream back to every replica within ~100ms.
//
// One singleton per process. The replica file is gitignored at
// `data/replica.db` (relative to the project root). On first start,
// the file is created and back-filled from the cloud DB; subsequent
// reads are zero-network until a write happens.
//
// In CI / Vitest runs we skip the embedded replica (no replica path)
// and use a direct cloud client — keeps tests hermetic and avoids
// committing a binary file. The schema is compiled to a no-op when
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

  const useReplica = process.env.NODE_ENV !== "test" && !process.env.RADON_DB_NO_REPLICA;

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

// Convenience: ensure the replica is fully synced before the caller
// reads. Useful at process startup; not required for normal reads
// (background sync keeps the file fresh).
export async function syncDb(): Promise<void> {
  const db = getDb();
  if ("sync" in db && typeof (db as { sync?: () => Promise<unknown> }).sync === "function") {
    await (db as { sync: () => Promise<unknown> }).sync();
  }
}

// Test seam — drop the cached client between vitest tests.
export function __resetDbForTests(): void {
  cached = null;
}
