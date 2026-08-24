export const RECONNECT_BASE_MS = 5_000;
export const RECONNECT_MAX_MS = 120_000;

/**
 * R-167: the reconnect was a flat 5 s with no backoff, jitter or cap, and
 * `scheduleReconnect` re-arms from its own failure branch — so a Gateway that
 * is down or sitting at 2FA was dialled 720 times an hour indefinitely, each
 * attempt a fresh TCP connect + IB API handshake. Exponential with full
 * jitter, capped: a fleet of relays never resonates on the same second.
 *
 * @param {number} attempt consecutive failed reconnects (0 = first retry)
 * @returns {number} delay in ms
 */
export function reconnectDelayMs(attempt, random = Math.random) {
  const exponent = Math.min(Math.max(attempt, 0), 10);
  const base = Math.min(RECONNECT_BASE_MS * 2 ** exponent, RECONNECT_MAX_MS);
  // Jitter upward by up to 20%, then clamp: the floor stays predictable so a
  // healthy reconnect is still prompt, the spread de-syncs a fleet, and the
  // cap is a real cap.
  return Math.min(Math.round(base * (1 + 0.2 * random())), RECONNECT_MAX_MS);
}

/** Generation-aware single-flight timer for IB socket reconnects. */
export function createReconnectGate({
  delayMs,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
}) {
  let timer = null;
  let generation = 0;

  return {
    schedule(callback, scheduleDelayMs = delayMs) {
      if (timer !== null) return false;
      const scheduledGeneration = generation;
      timer = setTimer(() => {
        timer = null;
        if (scheduledGeneration !== generation) return;
        callback();
      }, scheduleDelayMs);
      return true;
    },

    invalidate() {
      generation += 1;
      if (timer !== null) {
        clearTimer(timer);
        timer = null;
      }
    },

    get pending() {
      return timer !== null;
    },
  };
}
