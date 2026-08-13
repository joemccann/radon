#!/usr/bin/env node
// One-shot migration for the relative-image-url regression. Scans
// posts.json + every archive file under web/public/data/archive, rewrites
// any `/media/<file>` entry to `https://media.radon.run/<file>`, and
// reports a per-file count.
//
// IDEMPOTENT: posts already carrying absolute URLs are left untouched.
// Re-running is safe and produces a `relative=0` report.
//
// DRY-RUN BY DEFAULT. Pass `--apply` to actually write.
//
// Usage:
//   node scripts/newsfeed/migrate_relative_image_urls.js          # dry-run
//   node scripts/newsfeed/migrate_relative_image_urls.js --apply  # writes

import path from "node:path";
import { fileURLToPath } from "node:url";
import fs from "fs-extra";

import { absolutizeMediaUrl, MEDIA_ORIGIN } from "./media.js";
import { atomicWriteText } from "./atomic.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const DEFAULT_POSTS_FILE = path.join(PROJECT_ROOT, "web/public/data/posts.json");
const DEFAULT_ARCHIVE_DIR = path.join(PROJECT_ROOT, "web/public/data/archive");

// Pure transform — returns { posts, rewrites } so callers can dry-run on
// any in-memory list (the tests use this directly).
export function migratePosts(posts) {
  if (!Array.isArray(posts)) return { posts: [], rewrites: 0 };

  let rewrites = 0;
  const migrated = posts.map((post) => {
    if (!post || !Array.isArray(post.images) || post.images.length === 0) return post;

    let changed = false;
    const next = post.images.map((src) => {
      const after = absolutizeMediaUrl(src);
      if (after !== src) {
        changed = true;
        rewrites += 1;
      }
      return after;
    });

    return changed ? { ...post, images: next } : post;
  });

  return { posts: migrated, rewrites };
}

async function migrateFile(filePath, { apply }) {
  const exists = await fs.pathExists(filePath);
  if (!exists) {
    return { filePath, exists: false, rewrites: 0, totalImages: 0, totalPosts: 0 };
  }

  const raw = await fs.readFile(filePath, "utf8");
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    return { filePath, exists: true, error: `parse failed: ${err.message}` };
  }

  if (!Array.isArray(parsed)) {
    return { filePath, exists: true, error: "not a JSON array" };
  }

  const totalImages = parsed.reduce(
    (sum, p) => sum + (Array.isArray(p.images) ? p.images.length : 0),
    0,
  );
  const { posts, rewrites } = migratePosts(parsed);

  if (rewrites > 0 && apply) {
    // Preserve indentation of the original file (the scraper writes
    // 2-space pretty-printed JSON; matching that keeps diffs sane).
    await atomicWriteText(filePath, JSON.stringify(posts, null, 2), {
      backupPath: `${filePath}.bak`,
    });
  }

  return {
    filePath,
    exists: true,
    rewrites,
    totalImages,
    totalPosts: parsed.length,
  };
}

async function listArchiveFiles(archiveDir) {
  const exists = await fs.pathExists(archiveDir);
  if (!exists) return [];
  const entries = await fs.readdir(archiveDir);
  return entries
    .filter((name) => name.endsWith(".json"))
    .map((name) => path.join(archiveDir, name));
}

export async function runMigration({
  postsFile = DEFAULT_POSTS_FILE,
  archiveDir = DEFAULT_ARCHIVE_DIR,
  apply = false,
  log = console.log,
} = {}) {
  const archiveFiles = await listArchiveFiles(archiveDir);
  const targets = [postsFile, ...archiveFiles];
  const results = [];

  log(`[migrate] mode=${apply ? "APPLY" : "dry-run"} media-origin=${MEDIA_ORIGIN}`);
  log(`[migrate] scanning ${targets.length} file(s)`);

  let totalRewrites = 0;
  for (const file of targets) {
    const result = await migrateFile(file, { apply });
    results.push(result);

    if (result.error) {
      log(`[migrate] ${path.relative(PROJECT_ROOT, file)}  SKIP  ${result.error}`);
      continue;
    }
    if (!result.exists) {
      log(`[migrate] ${path.relative(PROJECT_ROOT, file)}  (absent)`);
      continue;
    }

    totalRewrites += result.rewrites;
    log(
      `[migrate] ${path.relative(PROJECT_ROOT, file)}  ` +
        `posts=${result.totalPosts} images=${result.totalImages} ` +
        `relative-rewritten=${result.rewrites}`,
    );
  }

  log(
    `[migrate] done. total relative URLs ${apply ? "rewritten" : "that WOULD be rewritten"}: ${totalRewrites}`,
  );

  return { results, totalRewrites };
}

function isDirectExecution() {
  if (!process.argv[1]) return false;
  return process.argv[1] === fileURLToPath(import.meta.url);
}

if (isDirectExecution()) {
  const apply = process.argv.includes("--apply");
  runMigration({ apply })
    .then(() => process.exit(0))
    .catch((err) => {
      console.error(`[migrate] fatal: ${err.message}`);
      process.exit(1);
    });
}
