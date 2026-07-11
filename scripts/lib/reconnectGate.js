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
