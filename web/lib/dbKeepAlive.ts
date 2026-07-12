import { dbExecute } from "@/lib/dbExecute";

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
    // dbExecute is the only chokepoint that both bounds the call (3s) and
    // self-heals a wedged undici pool. A bare getDb().execute here would hang
    // to undici's ~300s body timeout and never recover until process restart.
    try {
      await dbExecute("SELECT 1", { timeoutMs: 3_000, label: "keepalive" });
    } catch {
      // dbExecute already called resetDb on failure.
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
