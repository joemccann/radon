// Next.js server-process hook — runs once when the Node server boots.
// Used to keep the Turso connection pool warm so uncached DB reads/writes stay
// on the ~12ms warm path instead of paying the ~60-80ms cold TLS handshake.
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  try {
    const { syncSetupCompleteFromMarker } = await import("@/lib/setup/setupComplete");
    await syncSetupCompleteFromMarker();
  } catch (err) {
    console.warn("[instrumentation] setup-complete sync failed:", err);
  }
  // Only the long-lived Node.js production server has a process + undici pool
  // worth warming. Skip Edge runtime, the build, dev, and CI.
  if (process.env.NODE_ENV !== "production") return;
  // Vercel serverless functions are frozen between invocations: background
  // timers fire in bursts on thaw, so the loop-lag monitor reports the freeze
  // as a blocked loop and the keep-alive ping resets the pool mid-request.
  if (process.env.VERCEL !== "1") {
    try {
      const { startDbKeepAlive } = await import("@/lib/dbKeepAlive");
      startDbKeepAlive();
    } catch (err) {
      // Never let instrumentation crash server boot.
      console.warn("[instrumentation] db keep-alive failed to start:", err);
    }
    try {
      const { startLoopLagMonitor } = await import("@/lib/loopLagMonitor");
      startLoopLagMonitor();
    } catch (err) {
      console.warn("[instrumentation] loop-lag monitor failed to start:", err);
    }
  }
  try {
    const { installBoundedShutdown } = await import("@/lib/boundedShutdown");
    installBoundedShutdown();
  } catch (err) {
    console.warn("[instrumentation] bounded shutdown failed to install:", err);
  }
}
