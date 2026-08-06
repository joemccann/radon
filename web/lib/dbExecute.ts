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
import { getDb, resetDb, getPoolStats } from "./db";
import { withTimeout } from "./asyncTimeout";
import {
  createDbOperationIdentity,
  currentDbOperationIdentity,
  runWithDbOperation,
} from "./dbOperation";

/** Default per-read deadline. */
export const DEFAULT_DB_READ_TIMEOUT_MS = 3_000;

/** Undici buries the discriminator (ECONNRESET vs ClientDestroyedError vs
 * SocketError) in `err.cause` — sometimes in `.code`, sometimes only in the
 * name/message — and a bare "fetch failed" in journald is undiagnosable. */
export function describeDbError(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  const cause = err instanceof Error ? (err.cause as { code?: string; name?: string; message?: string } | undefined) : undefined;
  if (!cause) return message;
  const discriminator = [cause.code, cause.name, cause.message]
    .filter((part) => typeof part === "string" && part.length > 0)
    .join(" ");
  return discriminator ? `${message} (cause: ${discriminator})` : message;
}

export async function dbExecute(
  stmt: InStatement,
  opts: { timeoutMs?: number; label?: string } = {},
): Promise<ResultSet> {
  const { timeoutMs = DEFAULT_DB_READ_TIMEOUT_MS, label = "db" } = opts;
  const identity = currentDbOperationIdentity() ?? createDbOperationIdentity();
  identity.label ??= label;
  try {
    return await runWithDbOperation(identity, () =>
      withTimeout(
        getDb().execute(stmt),
        timeoutMs,
        `${label} read timed out after ${timeoutMs}ms`,
      ),
    );
  } catch (err) {
    console.warn(`[dbExecute:${label}] ${describeDbError(err)}${poolStatsSuffix()}`);
    resetDb(identity);
    throw err;
  }
}

/** Best-effort — route tests mock @/lib/db wholesale without getPoolStats,
 * and vitest's mock proxy THROWS on missing-export access, so this must
 * never let diagnostics break the failure path. */
function poolStatsSuffix(): string {
  try {
    const stats = getPoolStats();
    return stats ? ` pool=${JSON.stringify(stats)}` : "";
  } catch {
    return "";
  }
}
