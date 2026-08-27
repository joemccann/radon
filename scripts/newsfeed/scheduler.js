function abortableSleep(ms, signal) {
  if (ms <= 0) return Promise.resolve();
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      resolve();
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function runForever({ intervalMs, scrapeOnce, signal, onCycleError, maxConsecutiveErrors = 3 }) {
  if (typeof scrapeOnce !== "function") {
    throw new Error("runForever: scrapeOnce must be a function");
  }
  if (!Number.isFinite(intervalMs) || intervalMs <= 0) {
    throw new Error("runForever: intervalMs must be a positive number");
  }

  let consecutiveErrors = 0;
  while (!signal?.aborted) {
    const start = Date.now();
    try {
      await scrapeOnce();
      consecutiveErrors = 0;
    } catch (err) {
      consecutiveErrors += 1;
      if (onCycleError) onCycleError(err);
      else console.error(`[newsfeed] cycle failed: ${err.message}`);
      if (consecutiveErrors >= maxConsecutiveErrors) {
        throw new Error(`newsfeed failed ${consecutiveErrors} consecutive cycles`, { cause: err });
      }
    }
    if (signal?.aborted) return;
    const elapsed = Date.now() - start;
    const wait = Math.max(0, intervalMs - elapsed);
    await abortableSleep(wait, signal);
  }
}

export const SHUTDOWN_GRACE_MS = 10_000;

/**
 * Bounded shutdown for the long-running loop. A Playwright scrape mid-flight
 * does not observe the abort, so a SIGTERM that only aborted the controller
 * left the process alive until systemd's SIGKILL (90 s) — longer than the
 * deploy waits for the unit to go inactive. Abort, then exit after the grace.
 */
export function createShutdown({
  controller,
  exit = process.exit,
  graceMs = SHUTDOWN_GRACE_MS,
  /** () => boolean — true while a scrape cycle is mid-flight. */
  isCycleInFlight = () => false,
}) {
  let started = false;
  return function shutdown(signalName) {
    if (started) return;
    started = true;
    console.info(`[newsfeed] received ${signalName} — shutting down`);
    controller.abort();
    const timer = setTimeout(() => {
      // Exit 0 for a clean stop, NON-ZERO when the grace expired with a cycle
      // still running. The abort signal is only checked between cycles, so a
      // SIGTERM landing mid-cycle kills the process between persistPosts
      // (disk) and upsertPosts (Turso) — and exiting 0 made systemd's
      // Restart=on-failure treat that truncated cycle as a clean stop, with
      // nothing but a console.warn recording that work was dropped. R-262.
      const truncated = isCycleInFlight();
      if (truncated) {
        console.warn(
          `[newsfeed] shutdown grace of ${graceMs}ms elapsed MID-CYCLE — exiting non-zero; `
          + "a cycle was truncated between the disk write and the Turso upsert",
        );
      } else {
        console.warn(`[newsfeed] shutdown grace of ${graceMs}ms elapsed — exiting`);
      }
      exit(truncated ? 75 : 0);
    }, graceMs);
    timer.unref?.();
  };
}
