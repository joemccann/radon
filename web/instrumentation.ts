// Next.js server-process hook — runs once when the Node server boots.
// Used to keep the Turso connection pool warm so uncached DB reads/writes stay
// on the ~12ms warm path instead of paying the ~60-80ms cold TLS handshake.
export async function register() {
  // Only the long-lived Node.js production server has a process + undici pool
  // worth warming. Skip Edge runtime, the build, dev, and CI.
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  if (process.env.NODE_ENV !== "production") return;
  try {
    const { startDbKeepAlive } = await import("@/lib/dbKeepAlive");
    startDbKeepAlive();
  } catch (err) {
    // Never let instrumentation crash server boot.
    console.warn("[instrumentation] db keep-alive failed to start:", err);
  }
}
