/**
 * Bounded process exit for the production Next.js server.
 *
 * Next's own SIGTERM handler runs `server.close()`, which waits for every open
 * connection to finish. During RTH those include `radonFetch` calls with up to
 * 130 s timeouts against a FastAPI the deploy has already stopped, so the unit
 * sat in `final-sigterm` until systemd's 90 s SIGKILL while the deploy waited
 * only 60 s for it to go inactive (2026-08-24: three rollbacks). Next's cleanup
 * still runs first; this only caps how long the drain may take.
 */
import type { EventEmitter } from "node:events";

export const SHUTDOWN_GRACE_MS = 10_000;

const SIGNAL_EXIT_CODES: Record<string, number> = { SIGINT: 130, SIGTERM: 143 };

export interface BoundedShutdownOptions {
  proc?: Pick<EventEmitter, "on">;
  exit?: (code: number) => void;
  graceMs?: number;
  signals?: string[];
}

export function installBoundedShutdown({
  proc = process,
  exit = (code) => process.exit(code),
  graceMs = SHUTDOWN_GRACE_MS,
  signals = ["SIGTERM", "SIGINT"],
}: BoundedShutdownOptions = {}): void {
  let armed = false;
  const arm = (signal: string) => {
    if (armed) return;
    armed = true;
    const timer = setTimeout(() => {
      console.warn(`[shutdown] ${signal} grace of ${graceMs}ms elapsed with connections still draining — exiting`);
      exit(SIGNAL_EXIT_CODES[signal] ?? 128);
    }, graceMs);
    timer.unref?.();
  };
  for (const signal of signals) proc.on(signal, () => arm(signal));
}
