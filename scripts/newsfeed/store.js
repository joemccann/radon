import path from "path";
import fs from "fs-extra";
import { absolutizeMediaUrl, MEDIA_ORIGIN } from "./media.js";
import { atomicWriteText, withFileLock } from "./atomic.js";

export const MAX_POSTS_FILE_BYTES = 500 * 1024;

// Defensive guard at the persistence seam. Any post arriving here MUST
// carry absolute media URLs — the dashboard, Turso, and image hosts all
// depend on it. Anything we can rewrite is rewritten; anything we can't
// is dropped from the images array and logged once so the contract bug
// surfaces without breaking the cycle.
function normalisePostImageUrls(post) {
  if (!post || typeof post !== "object") return post;
  if (!Array.isArray(post.images) || post.images.length === 0) return post;

  const cleaned = [];
  for (const src of post.images) {
    const next = absolutizeMediaUrl(src);
    if (typeof next === "string" && (next.startsWith("https://") || next.startsWith("http://"))) {
      cleaned.push(next);
    } else {
      console.warn(
        `[newsfeed] dropping non-absolute image url for post=${post.id}: ${JSON.stringify(src)} ` +
          `(expected ${MEDIA_ORIGIN}/<file> or http(s)://...)`,
      );
    }
  }

  if (cleaned.length === post.images.length && cleaned.every((v, i) => v === post.images[i])) {
    return post;
  }
  return { ...post, images: cleaned };
}

function normaliseTimestamp(ts) {
  const date = new Date(ts);
  const ms = date.getTime();
  return Number.isFinite(ms) ? { iso: date.toISOString(), ms } : { iso: ts, ms: 0 };
}

export async function loadExistingPosts(postsFile) {
  try {
    const raw = await fs.readFile(postsFile, "utf8");
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new TypeError("newsfeed posts file must contain a JSON array");
    return parsed.map((item) => {
      const { ms } = normaliseTimestamp(item.timestamp);
      return {
        ...item,
        timestampMs: ms,
        rawImages: Array.isArray(item.rawImages) ? item.rawImages : [],
      };
    });
  } catch (err) {
    if (err.code === "ENOENT") return [];
    if (err instanceof SyntaxError || err instanceof TypeError) {
      const quarantine = `${postsFile}.corrupt-${Date.now()}`;
      await fs.move(postsFile, quarantine, { overwrite: false }).catch(() => {});
      err.message = `${err.message}; quarantined=${quarantine}`;
    }
    throw err;
  }
}

export function mergePosts(existingPosts, scrapedPosts, { now = () => new Date() } = {}) {
  const byId = new Map(existingPosts.map((post) => [post.id, post]));
  let changed = false;

  scrapedPosts.forEach((post) => {
    const ts = normaliseTimestamp(post.timestamp);
    const base = {
      id: post.id,
      title: post.title,
      content: post.content,
      timestamp: ts.iso,
      timestampMs: ts.ms,
      rawImages: Array.isArray(post.images) ? post.images : [],
    };

    const existing = byId.get(post.id);
    if (!existing) {
      const stamp = now().toISOString();
      byId.set(post.id, {
        ...base,
        images: [],
        createdAt: stamp,
        updatedAt: stamp,
      });
      changed = true;
      return;
    }

    const needsUpdate =
      existing.title !== base.title ||
      existing.content !== base.content ||
      existing.timestamp !== base.timestamp ||
      JSON.stringify(existing.rawImages || []) !== JSON.stringify(base.rawImages);

    if (needsUpdate) {
      byId.set(post.id, {
        ...existing,
        ...base,
        updatedAt: now().toISOString(),
      });
      changed = true;
    }
  });

  const merged = Array.from(byId.values()).sort(
    (a, b) => (b.timestampMs || 0) - (a.timestampMs || 0),
  );

  return { merged, changed };
}

export async function persistPosts(posts, opts) {
  const {
    dataDir,
    archiveDir,
    mediaDir,
    postsFile,
    maxBytes = MAX_POSTS_FILE_BYTES,
    now = () => new Date(),
  } = opts;

  return withFileLock(postsFile, async () => {
    await fs.ensureDir(dataDir);
    await fs.ensureDir(archiveDir);
    if (mediaDir) await fs.ensureDir(mediaDir);

    const reduced = posts
      .map((post) => normalisePostImageUrls(post))
      .map(({ timestampMs, ...rest }) => rest);
    const output = JSON.stringify(reduced, null, 2);
    JSON.parse(output);
    const size = Buffer.byteLength(output, "utf8");

    if (size <= maxBytes) {
      await atomicWriteText(postsFile, output);
      return { archived: false };
    }

    const archiveName = `posts-${now().toISOString().replace(/[:.]/g, "-")}.json`;
    const archivePath = path.join(archiveDir, archiveName);
    await atomicWriteText(archivePath, output);

    const keepCount = Math.max(1, Math.ceil(posts.length * 0.2));
    const truncated = reduced.slice(0, keepCount);
    await atomicWriteText(postsFile, JSON.stringify(truncated, null, 2));

    return { archived: true, archivePath, archiveName, keepCount };
  });
}

// Test seam — exercise the URL normaliser without spinning up the full
// persistence pipeline.
export const __normalisePostImageUrls = normalisePostImageUrls;
