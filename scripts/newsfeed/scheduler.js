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

export async function runForever({ intervalMs, scrapeOnce, signal, onCycleError }) {
  if (typeof scrapeOnce !== "function") {
    throw new Error("runForever: scrapeOnce must be a function");
  }
  if (!Number.isFinite(intervalMs) || intervalMs <= 0) {
    throw new Error("runForever: intervalMs must be a positive number");
  }

  while (!signal?.aborted) {
    const start = Date.now();
    try {
      await scrapeOnce();
    } catch (err) {
      if (onCycleError) onCycleError(err);
      else console.error(`[newsfeed] cycle failed: ${err.message}`);
    }
    if (signal?.aborted) return;
    const elapsed = Date.now() - start;
    const wait = Math.max(0, intervalMs - elapsed);
    await abortableSleep(wait, signal);
  }
}
