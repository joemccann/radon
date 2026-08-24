// Push newsfeed images from the laptop's local cache to the Hetzner
// media volume so https://media.radon.run/<file>.png serves them.
//
// Called from scripts/newsfeed/index.js after each scrape cycle.
// Idempotent (rsync only copies new/changed files). Non-fatal — if
// Tailscale is down, the cycle continues and the next cycle retries.

import { spawn } from "node:child_process";
import {
  ensurePublicMediaPermissions,
  localMediaDest,
  pruneMediaTree,
} from "./mediaPermissions.js";

// Default target uses Tailscale's MagicDNS name `ib-gateway` — secure private route.
// Operators without Tailscale on the laptop can switch to the Hetzner public IP via
//   RADON_MEDIA_REMOTE=<user>@<prod-host>:/path/to/media/
// Same SSH key is authorized on both routes (single ~/.ssh/authorized_keys on the VPS).
// See docs/cloud-services.md "Tailscale-free media push".
const REMOTE = process.env.RADON_MEDIA_REMOTE ?? "radon@ib-gateway:/home/radon/radon-cloud/media/";
const LOCAL = process.env.RADON_MEDIA_LOCAL ?? "web/public/media/";
const RSYNC_TIMEOUT_MS = 30_000;
// R-171: the full-tree sweep runs at most once an hour, not every cycle.
export const SWEEP_MIN_INTERVAL_MS = 60 * 60_000;
let lastSweepAt = 0;

export async function pushMedia({
  local = LOCAL,
  remote = REMOTE,
  timeoutMs = RSYNC_TIMEOUT_MS,
} = {}) {
  const result = await new Promise((resolve) => {
    // R-137: `--ignore-existing` skipped every pre-fix 0600 image forever, so
    // they stayed 403 on media.radon.run permanently, and the post-transfer
    // chmod below never runs on the DEFAULT remote route (localMediaDest is
    // null for `radon@ib-gateway:/…`). rsync's own `--chmod` is the only
    // thing that reaches the remote destination. Dropping --ignore-existing
    // costs nothing: filenames are content-derived and immutable, so the
    // size+mtime check skips the data and `-a` still repairs the mode.
    const args = [
      "-az",
      "--chmod=F644",        // destination file mode, applied over SSH too
      "--itemize-changes",   // emit a line per transferred file
      "--timeout=20",         // per-file network timeout
      local,
      remote,
    ];

    const proc = spawn("rsync", args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let killed = false;

    if (proc.stdout) proc.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    if (proc.stderr) proc.stderr.on("data", (chunk) => { stderr += chunk.toString(); });

    const timer = setTimeout(() => {
      killed = true;
      proc.kill("SIGKILL");
    }, timeoutMs);

    proc.on("close", (code) => {
      clearTimeout(timer);
      if (killed) {
        resolve({ ok: false, reason: `timed out after ${timeoutMs}ms` });
        return;
      }
      if (code !== 0) {
        // rsync exit 23/24 ("partial transfer due to vanished source files")
        // are not fatal. Anything else, fail soft and let the next cycle retry.
        const reason = `rsync exit ${code}: ${stderr.trim().slice(0, 200) || stdout.trim().slice(0, 200) || "(no output)"}`;
        resolve({ ok: code === 23 || code === 24, reason });
        return;
      }
      const transferred = stdout.split("\n").filter((l) => /^>f/.test(l)).length;
      resolve({ ok: true, transferred });
    });

    proc.on("error", (err) => {
      clearTimeout(timer);
      resolve({ ok: false, reason: `rsync spawn error: ${err.message}` });
    });
  });

  // Files written under UMask=0077 land 0600; Caddy 403s them. Which
  // mechanism repaired the mode is recorded either way, so a remote push is
  // never silently unrepaired.
  if (result.ok) {
    const dest = localMediaDest(remote);
    if (dest) {
      result.repairMode = "local-chmod";
      // R-171: readdir + stat over the WHOLE tree on every 2-minute cycle, at
      // a cost growing linearly in a directory that only grows. rsync's own
      // --chmod already sets the mode on transferred files; this sweep is the
      // backstop for files written before that landed, so it does not need to
      // run 720 times a day.
      if (Date.now() - lastSweepAt >= SWEEP_MIN_INTERVAL_MS) {
        lastSweepAt = Date.now();
        try {
          result.repaired = await ensurePublicMediaPermissions(dest);
          result.pruned = await pruneMediaTree(dest);
        } catch (err) {
          result.repaired = 0;
          result.repairError = err instanceof Error ? err.message : String(err);
        }
      } else {
        result.repairMode = "local-chmod-skipped";
      }
    } else {
      result.repairMode = "rsync-chmod";
    }
  }
  return result;
}

// Run directly: `bun run scripts/newsfeed/push_media.js [local] [remote]`
if (import.meta.url === `file://${process.argv[1]}`) {
  const [, , localArg, remoteArg] = process.argv;
  const t0 = Date.now();
  const result = await pushMedia({
    local: localArg ?? LOCAL,
    remote: remoteArg ?? REMOTE,
  });
  const ms = Date.now() - t0;
  if (result.ok) {
    console.log(`[push-media] ok ${ms}ms transferred=${result.transferred ?? 0}`);
  } else {
    console.warn(`[push-media] non-fatal: ${result.reason} (${ms}ms)`);
  }
}
