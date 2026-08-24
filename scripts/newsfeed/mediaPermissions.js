import path from "path";
import fs from "fs-extra";

// Caddy serves this tree as a different user than radon-newsfeed. systemd
// UMask=0077 (SEC-041, Playwright session 0600) masks writeFile's mode, so a
// 0644 request lands as 0600 and media.radon.run 403s. chmod after write
// beats the umask. Keep session/cookie writes on the default umask.
export const PUBLIC_MEDIA_FILE_MODE = 0o644;

export async function writePublicMediaFile(destPath, data) {
  await fs.writeFile(destPath, data, { mode: PUBLIC_MEDIA_FILE_MODE });
  await fs.chmod(destPath, PUBLIC_MEDIA_FILE_MODE);
}

export function localMediaDest(remote) {
  if (typeof remote !== "string" || remote.length === 0) return null;
  // user@host:path — leave those to rsync/ssh. A leading slash is a local tree
  // (Hetzner sets RADON_MEDIA_REMOTE=/home/radon/radon-cloud/media/).
  if (remote.includes(":") && !remote.startsWith("/")) return null;
  return remote.endsWith("/") ? remote.slice(0, -1) : remote;
}

// R-137: the sweep chmods files in an OPERATOR-SUPPLIED directory. Without
// an allowlist a stray storageState / cookie jar under a Caddy web root
// becomes world-readable. Only the shapes the newsfeed actually publishes.
export const PUBLIC_MEDIA_EXTENSIONS = new Set([
  ".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".svg",
]);

export function isPublicMediaFile(name) {
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return false;
  return PUBLIC_MEDIA_EXTENSIONS.has(name.slice(dot).toLowerCase());
}

// R-171: the media tree had no pruner ANYWHERE — no unlink, no tmpfiles.d —
// so every image the scraper ever downloaded was retained forever as a
// re-encoded PNG at compressionLevel 9, up to 16 MP. The disk watchdog is
// alert-only and its own message says "prune caches/logs before it wedges the
// box"; for this tree there was nothing to prune with. Filenames are
// content-derived and immutable, so age is the only usable key.
export const MEDIA_RETENTION_DAYS = 90;
export const MAX_MEDIA_FILES = 5000;

export async function pruneMediaTree(dir, { now = Date.now() } = {}) {
  if (!dir) return 0;
  let entries;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch (err) {
    if (err && err.code === "ENOENT") return 0;
    throw err;
  }
  const cutoff = now - MEDIA_RETENTION_DAYS * 24 * 60 * 60 * 1000;
  const survivors = [];
  let removed = 0;
  for (const entry of entries) {
    if (!entry.isFile() || !isPublicMediaFile(entry.name)) continue;
    const target = path.join(dir, entry.name);
    let stat;
    try {
      stat = await fs.stat(target);
    } catch {
      continue;
    }
    if (stat.mtimeMs < cutoff) {
      try {
        await fs.unlink(target);
        removed += 1;
      } catch {
        // A file that vanished under us is already pruned.
      }
      continue;
    }
    survivors.push({ target, mtimeMs: stat.mtimeMs });
  }
  if (survivors.length > MAX_MEDIA_FILES) {
    survivors.sort((a, b) => a.mtimeMs - b.mtimeMs);
    for (const { target } of survivors.slice(0, survivors.length - MAX_MEDIA_FILES)) {
      try {
        await fs.unlink(target);
        removed += 1;
      } catch {
        // ignore
      }
    }
  }
  return removed;
}

export async function ensurePublicMediaPermissions(dir) {
  if (!dir) return 0;
  let entries;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch (err) {
    if (err && err.code === "ENOENT") return 0;
    throw err;
  }
  let repaired = 0;
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    if (!isPublicMediaFile(entry.name)) continue;
    const destPath = path.join(dir, entry.name);
    const st = await fs.stat(destPath);
    if ((st.mode & 0o777) === PUBLIC_MEDIA_FILE_MODE) continue;
    await fs.chmod(destPath, PUBLIC_MEDIA_FILE_MODE);
    repaired += 1;
  }
  return repaired;
}
