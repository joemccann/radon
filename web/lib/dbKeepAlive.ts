import { getDb, resetDb } from "@/lib/db";
import {
  createDbOperationIdentity,
  runWithDbOperation,
} from "@/lib/dbOperation";

// undici (the @libsql HTTP client's transport) closes idle sockets after ~4s,
// but the app polls Turso on ~60s intervals — so without warming, every
// uncached read/write opens a fresh connection and pays the full TLS handshake.
// Measured on the VPS: cold ~60-80ms vs warm ~11-15ms per query. A cheap
// SELECT 1 every few seconds keeps one socket alive so ALL uncached DB
// operations stay on the warm path (~5x faster than cold), not just cache hits.
const KEEPALIVE_INTERVAL_MS = 3_000;

/**
 * Start a background heartbeat that keeps the Turso connection pool warm.
 *
 * On a failed ping it drops the cached client (same self-heal the API routes
 * use) so the next real request rebuilds a fresh connection rather than reusing
 * a wedged socket. It never writes ``service_health`` or touches the degraded
 * banner — a real outage still surfaces through the routes' own read attempts.
 *
 * Returns a stop function. The interval is ``unref``'d so it never keeps the
 * Node process alive on its own.
 */
export function startDbKeepAlive(intervalMs: number = KEEPALIVE_INTERVAL_MS): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const schedule = () => {
    if (stopped) return;
    timer = setTimeout(run, intervalMs);
    timer.unref?.();
  };

  const run = async () => {
    const identity = createDbOperationIdentity();
    try {
      await runWithDbOperation(identity, () => getDb().execute("SELECT 1"));
    } catch {
      // Shares its identity with the proxy, so this prompt recovery is counted
      // once even though both layers observe the same rejection.
      resetDb(identity);
    } finally {
      schedule();
    }
  };

  schedule();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}
