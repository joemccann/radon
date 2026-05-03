// Push newsfeed images from the laptop's local cache to the Hetzner
// media volume so https://media.radon.run/<file>.png serves them.
//
// Called from scripts/newsfeed/index.js after each scrape cycle.
// Idempotent (rsync only copies new/changed files). Non-fatal — if
// Tailscale is down, the cycle continues and the next cycle retries.

import { spawn } from "node:child_process";

// Default target uses Tailscale's MagicDNS name `ib-gateway` — secure private route.
// Operators without Tailscale on the laptop can switch to the Hetzner public IP via
//   RADON_MEDIA_REMOTE=radon@5.78.148.38:/home/radon/radon-cloud/media/
// Same SSH key is authorized on both routes (single ~/.ssh/authorized_keys on the VPS).
// See docs/cloud-services.md "Tailscale-free media push".
const REMOTE = process.env.RADON_MEDIA_REMOTE ?? "radon@ib-gateway:/home/radon/radon-cloud/media/";
const LOCAL = process.env.RADON_MEDIA_LOCAL ?? "web/public/media/";
const RSYNC_TIMEOUT_MS = 30_000;

export async function pushMedia({
  local = LOCAL,
  remote = REMOTE,
  timeoutMs = RSYNC_TIMEOUT_MS,
} = {}) {
  return new Promise((resolve) => {
    const args = [
      "-az",
      "--ignore-existing",   // never overwrite — image filenames are content-derived, immutable
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
