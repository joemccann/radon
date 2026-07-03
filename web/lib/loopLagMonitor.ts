// Event-loop lag sentinel for the long-lived Next.js server process.
//
// The ~hourly 3s Turso read stalls are Node-local (a parallel Python read
// succeeds in the same minute). If the cause is a blocked event loop rather
// than a socket, EVERY pending withTimeout fires together while the process
// does no I/O — indistinguishable from a socket stall in the existing logs.
// This monitor disambiguates: a tick delivered later than `thresholdMs` past
// its schedule means the loop was blocked that long, logged in the same
// journald stream as the dbExecute failures for direct correlation.

type LoopLagOptions = {
  intervalMs?: number;
  thresholdMs?: number;
  now?: () => number;
  warn?: (message: string) => void;
};

export function startLoopLagMonitor(options: LoopLagOptions = {}): () => void {
  const {
    intervalMs = 500,
    thresholdMs = 1_000,
    now = Date.now,
    warn = console.warn,
  } = options;

  let expectedAt = now() + intervalMs;
  const timer = setInterval(() => {
    const lag = now() - expectedAt;
    if (lag > thresholdMs) {
      warn(`[loop-lag] event loop blocked ~${Math.round(lag)}ms`);
    }
    expectedAt = now() + intervalMs;
  }, intervalMs);
  (timer as { unref?: () => void }).unref?.();
  return () => clearInterval(timer);
}
