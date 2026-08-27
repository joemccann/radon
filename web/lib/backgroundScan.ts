/**
 * Stale-while-revalidate trigger shared by the GEX / VCG / regime GET routes.
 *
 * Dedupes while a scan is in flight and arms a backoff after a failure, so a
 * refused scan (FastAPI 429 inside its cooldown/backoff, or 502 on a lane-
 * exhausted host) is not re-requested on every 5 s client poll. Mirrors
 * SCAN_FAILURE_BACKOFF_S in scripts/api/scan_gate.py.
 */
export const BACKGROUND_SCAN_BACKOFF_MS = 60_000;

export interface BackgroundScanTriggerOptions {
  label: string;
  run: () => Promise<unknown>;
  backoffMs?: number;
  now?: () => number;
}

export type BackgroundScanTrigger = () => boolean;

export function createBackgroundScanTrigger({
  label,
  run,
  backoffMs = BACKGROUND_SCAN_BACKOFF_MS,
  now = Date.now,
}: BackgroundScanTriggerOptions): BackgroundScanTrigger {
  let inFlight = false;
  let blockedUntil = 0;

  return function trigger(): boolean {
    if (inFlight || now() < blockedUntil) return false;
    inFlight = true;
    console.log(`[${label}] Background scan triggered via FastAPI`);
    // These triggers are built once per process with
    // `run: () => radonFetch(...)`, and radonFetch constructs a `new URL`
    // from env — so a SYNCHRONOUS throw propagated straight out of trigger()
    // before any handler was attached and left `inFlight` true for the life
    // of the Node process. From then on every poll returned false with no
    // log, no backoff timestamp and no error, and the routes served their
    // last cached snapshot indefinitely while appearing to revalidate.
    // `run()` stays synchronous here on purpose; only the throw is caught.
    // R-256.
    let pending: Promise<unknown>;
    try {
      pending = run();
    } catch (err: unknown) {
      inFlight = false;
      blockedUntil = now() + backoffMs;
      const message = err instanceof Error ? err.message : String(err);
      console.error(`[${label}] Background scan threw synchronously (backing off ${backoffMs / 1000}s):`, message);
      return true;
    }
    pending
      .then(() => {
        console.log(`[${label}] Background scan complete`);
      })
      .catch((err: unknown) => {
        blockedUntil = now() + backoffMs;
        const message = err instanceof Error ? err.message : String(err);
        console.error(`[${label}] Background scan failed (backing off ${backoffMs / 1000}s):`, message);
      })
      .finally(() => {
        inFlight = false;
      });
    return true;
  };
}
