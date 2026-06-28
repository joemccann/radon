// Bounded, self-healing Turso read — the canonical chokepoint every Next.js
// route should use instead of a bare `getDb().execute(...)`.
//
// In the Node runtime `@libsql/client` resolves `libsql://`/`https://` to an
// HTTP client whose undici keep-alive POOL can go stale (half-open sockets
// after an idle/NAT/edge drop). A reused dead socket then hangs the request
// until undici's ~300s body timeout, and because the client is a process-wide
// singleton, every route sharing it stalls at once until restart. `dbExecute`
// caps each call at `timeoutMs` AND drops the cached client on ANY failure
// (timeout included) so the next request rebuilds a fresh pool. Lives in its
// own module — separate from `./db` — so route tests that mock `@/lib/db`
// wholesale still exercise the real bounding logic against their mocked client.
// See `feedback_libsql_http_transport_no_wss_singleton`.

import type { InStatement, ResultSet } from "@libsql/client";
import { getDb, resetDb } from "./db";
import { withTimeout } from "./asyncTimeout";

/** Default per-read deadline. */
export const DEFAULT_DB_READ_TIMEOUT_MS = 3_000;

export async function dbExecute(
  stmt: InStatement,
  opts: { timeoutMs?: number; label?: string } = {},
): Promise<ResultSet> {
  const { timeoutMs = DEFAULT_DB_READ_TIMEOUT_MS, label = "db" } = opts;
  try {
    return await withTimeout(
      getDb().execute(stmt),
      timeoutMs,
      `${label} read timed out after ${timeoutMs}ms`,
    );
  } catch (err) {
    resetDb();
    throw err;
  }
}
