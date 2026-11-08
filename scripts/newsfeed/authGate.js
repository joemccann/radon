// When the scraper may run the product-cookie renewal (Firebase grant).
//
// One explicit state, one transition per event:
//   never   -> no renewal has run in this process: attempt.
//   fresh   -> renewed at `at`: attempt again once the re-auth window passes.
//   forced  -> a paywall stub asked for a renewal on the next cycle.
//   backoff -> the last renewal failed: wait until `until` (5m doubling to
//              60m) whatever else asks. Before this, a failed renewal left
//              the 0 sentinel in place and every 2-minute cycle hit Firebase.

export const DEFAULT_BASE_BACKOFF_MS = 5 * 60 * 1000;
export const DEFAULT_MAX_BACKOFF_MS = 60 * 60 * 1000;

export function createAuthGate({
  now = Date.now,
  reauthIntervalMs,
  baseBackoffMs = DEFAULT_BASE_BACKOFF_MS,
  maxBackoffMs = DEFAULT_MAX_BACKOFF_MS,
} = {}) {
  let state = { kind: "never" };

  return {
    state: () => ({ ...state }),

    /** { attempt: boolean, backoffUntil?: number } for this cycle. */
    decide() {
      const t = now();
      switch (state.kind) {
        case "never":
        case "forced":
          return { attempt: true };
        case "fresh":
          return { attempt: t - state.at >= reauthIntervalMs };
        case "backoff":
          return t >= state.until ? { attempt: true } : { attempt: false, backoffUntil: state.until };
        default:
          return { attempt: true };
      }
    },

    succeeded() {
      state = { kind: "fresh", at: now() };
    },

    failed() {
      const failures = state.kind === "backoff" ? state.failures + 1 : 1;
      const delay = Math.min(baseBackoffMs * 2 ** (failures - 1), maxBackoffMs);
      state = { kind: "backoff", failures, until: now() + delay };
    },

    requestReauth() {
      // A paywall stub cannot shorten a failure backoff.
      if (state.kind !== "backoff") state = { kind: "forced" };
    },
  };
}
