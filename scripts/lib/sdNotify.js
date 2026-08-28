/**
 * systemd watchdog ping, extracted so it is testable in isolation.
 *
 * Node's stdlib cannot open an AF_UNIX SOCK_DGRAM (node:dgram is UDP-only), so
 * the ping is delegated to `systemd-notify` on the host and to `socat` in the
 * app-plane node image, which has socat and no systemd.
 *
 * R-335: the socat path spawned a child with NO "error" listener and wrote to
 * its stdin immediately. A missing or unspawnable `socat` reports ENOENT
 * ASYNCHRONOUSLY, and `stdin.end()` additionally errors on the never-opened
 * pipe — both after the caller's `try` has returned, so neither could be
 * caught. Node raised ERR_UNHANDLED_ERROR and the realtime market-data process
 * exited: once at READY=1 and again every heartbeat, a crash loop that dropped
 * every WebSocket quote subscriber. Listeners are attached BEFORE the write,
 * and the socat path is disabled after the first ENOENT so the fallback is
 * attempted once rather than on every ping.
 */
import { execFile, spawn } from "node:child_process";

export function createSdNotifier({ socket, watchdogUsec, log = () => {} } = {}) {
  const enabled = Boolean(socket && watchdogUsec > 0);
  let socatUnavailable = false;

  function viaSocat(state) {
    if (socatUnavailable) return;
    let child;
    try {
      child = spawn("socat", ["-t0", "-", `UNIX-SENDTO:${socket}`], {
        stdio: ["pipe", "ignore", "ignore"],
      });
    } catch (err) {
      socatUnavailable = true;
      log(`sdNotify: socat unavailable (${err?.message ?? err})`);
      return;
    }

    // Attached BEFORE any write. An async ENOENT with no listener is a fatal
    // ERR_UNHANDLED_ERROR on an EventEmitter, not a caught exception.
    child.on("error", (err) => {
      if (err?.code === "ENOENT") socatUnavailable = true;
      log(`sdNotify: socat spawn failed (${err?.message ?? err})`);
    });
    child.stdin?.on("error", (err) => {
      log(`sdNotify: socat stdin failed (${err?.message ?? err})`);
    });

    try {
      child.stdin?.end(state);
    } catch (err) {
      log(`sdNotify: socat write failed (${err?.message ?? err})`);
    }
  }

  return function sdNotify(state) {
    if (!enabled) return;
    try {
      // Fire-and-forget; the callback swallows ENOENT / send errors so a
      // watchdog hiccup can never throw or block the event loop.
      execFile("systemd-notify", [state], (err) => {
        if (!err) return;
        viaSocat(state);
      });
    } catch {
      viaSocat(state);
    }
  };
}
