import path from "path";
import fs from "fs-extra";

export const MAX_POSTS_FILE_BYTES = 500 * 1024;

function normaliseTimestamp(ts) {
  const date = new Date(ts);
  const ms = date.getTime();
  return Number.isFinite(ms) ? { iso: date.toISOString(), ms } : { iso: ts, ms: 0 };
}

export async function loadExistingPosts(postsFile) {
  try {
    const raw = await fs.readFile(postsFile, "utf8");
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
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
    console.warn(`[newsfeed] failed to read existing posts: ${err.message}`);
    return [];
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

  await fs.ensureDir(dataDir);
  await fs.ensureDir(archiveDir);
  if (mediaDir) await fs.ensureDir(mediaDir);

  const reduced = posts.map(({ timestampMs, ...rest }) => rest);
  const output = JSON.stringify(reduced, null, 2);
  const size = Buffer.byteLength(output, "utf8");

  if (size <= maxBytes) {
    await fs.writeFile(postsFile, output);
    return { archived: false };
  }

  const archiveName = `posts-${now().toISOString().replace(/[:.]/g, "-")}.json`;
  const archivePath = path.join(archiveDir, archiveName);
  await fs.writeFile(archivePath, output);

  const keepCount = Math.max(1, Math.ceil(posts.length * 0.2));
  const truncated = reduced.slice(0, keepCount);
  await fs.writeFile(postsFile, JSON.stringify(truncated, null, 2));

  return { archived: true, archivePath, archiveName, keepCount };
}
