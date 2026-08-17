/**
 * Counted Unusual Whales fetch for Next.js route handlers.
 *
 * UWClient (Python) records every UW HTTP hit into the flock-shared daily
 * budget file, but route handlers that fetch UW directly incremented
 * nothing — browsing-driven traffic was invisible to /uw/usage and the
 * universe-scan brake (REL-036 / R-062). Every UW fetch from web MUST go
 * through this wrapper: it mirrors one hit into the shared counter via
 * FastAPI, fire-and-forget, after the UW response arrives — matching
 * UWClient, which counts responses, not connection failures.
 */
import { radonFetch } from "@/lib/radonApi";

const RECORD_TIMEOUT_MS = 3_000;

export async function countedUwFetch(
  input: string | URL,
  init?: RequestInit,
): Promise<Response> {
  const res = await fetch(input, init);
  recordBudgetHit();
  return res;
}

function recordBudgetHit(): void {
  void radonFetch("/uw/usage/record", {
    method: "POST",
    timeout: RECORD_TIMEOUT_MS,
  }).catch(() => {
    // Counting is telemetry — never let it affect the UW data path.
  });
}
