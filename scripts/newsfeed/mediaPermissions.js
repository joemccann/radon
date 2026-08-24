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
