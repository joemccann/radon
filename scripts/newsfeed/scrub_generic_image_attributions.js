#!/usr/bin/env node
// One-shot scrub for the generic-image-attribution regression.
//
// themarketear.com emits `https://themarketear.com/assets/images/generic.png`
// into JSON-LD `schema.image` for posts that have no real image in their DOM.
// Prior to the extract.js fix, the scraper honoured that schema fallback, and
// the downloader cache pinned every text-only post to whatever bytes lived
// behind that placeholder (the EMB candlestick chart on 2026-05-21).
//
// This scrub clears `images` and `rawImages` on any post whose `rawImages`
// includes that placeholder URL, so existing posts.json reflects the truth
// after the extract.js fix lands. IDEMPOTENT.
//
// DRY-RUN BY DEFAULT. Pass `--apply` to actually write.
//
// Usage:
//   node scripts/newsfeed/scrub_generic_image_attributions.js          # dry-run
//   node scripts/newsfeed/scrub_generic_image_attributions.js --apply  # writes

import path from "node:path";
import { fileURLToPath } from "node:url";
import fs from "fs-extra";
import { atomicWriteText } from "./atomic.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const DEFAULT_POSTS_FILE = path.join(PROJECT_ROOT, "web/public/data/posts.json");
const DEFAULT_ARCHIVE_DIR = path.join(PROJECT_ROOT, "web/public/data/archive");

const GENERIC_PLACEHOLDER_RE = /\/assets\/images\/generic\.png(?:[?#]|$)/i;

function hasGenericPlaceholder(urls) {
  if (!Array.isArray(urls)) return false;
  return urls.some((u) => typeof u === "string" && GENERIC_PLACEHOLDER_RE.test(u));
}

// Pure transform — returns { posts, scrubbed } so callers can dry-run on any
// in-memory list (the tests use this directly).
export function scrubPosts(posts) {
  if (!Array.isArray(posts)) return { posts: [], scrubbed: 0 };

  let scrubbed = 0;
  const next = posts.map((post) => {
    if (!post || !hasGenericPlaceholder(post.rawImages)) return post;
    scrubbed += 1;
    return { ...post, rawImages: [], images: [] };
  });

  return { posts: next, scrubbed };
}

async function scrubFile(filePath, { apply }) {
  const exists = await fs.pathExists(filePath);
  if (!exists) {
    return { filePath, exists: false, scrubbed: 0, totalPosts: 0 };
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

  const { posts, scrubbed } = scrubPosts(parsed);

  if (scrubbed > 0 && apply) {
    await atomicWriteText(filePath, JSON.stringify(posts, null, 2), {
      backupPath: `${filePath}.bak`,
    });
  }

  return { filePath, exists: true, scrubbed, totalPosts: parsed.length };
}

async function listArchiveFiles(archiveDir) {
  const exists = await fs.pathExists(archiveDir);
  if (!exists) return [];
  const entries = await fs.readdir(archiveDir);
  return entries
    .filter((name) => name.endsWith(".json"))
    .map((name) => path.join(archiveDir, name));
}

export async function runScrub({
  postsFile = DEFAULT_POSTS_FILE,
  archiveDir = DEFAULT_ARCHIVE_DIR,
  apply = false,
  log = console.log,
} = {}) {
  const archiveFiles = await listArchiveFiles(archiveDir);
  const targets = [postsFile, ...archiveFiles];

  log(`[scrub] mode=${apply ? "APPLY" : "dry-run"} placeholder=/assets/images/generic.png`);
  log(`[scrub] scanning ${targets.length} file(s)`);

  let totalScrubbed = 0;
  const results = [];
  for (const file of targets) {
    const result = await scrubFile(file, { apply });
    results.push(result);

    if (result.error) {
      log(`[scrub] ${path.relative(PROJECT_ROOT, file)}  SKIP  ${result.error}`);
      continue;
    }
    if (!result.exists) {
      log(`[scrub] ${path.relative(PROJECT_ROOT, file)}  (absent)`);
      continue;
    }

    totalScrubbed += result.scrubbed;
    log(
      `[scrub] ${path.relative(PROJECT_ROOT, file)}  ` +
        `posts=${result.totalPosts} scrubbed=${result.scrubbed}`,
    );
  }

  log(
    `[scrub] done. total posts ${apply ? "scrubbed" : "that WOULD be scrubbed"}: ${totalScrubbed}`,
  );

  return { results, totalScrubbed };
}

function isDirectExecution() {
  if (!process.argv[1]) return false;
  return process.argv[1] === fileURLToPath(import.meta.url);
}

if (isDirectExecution()) {
  const apply = process.argv.includes("--apply");
  runScrub({ apply })
    .then(() => process.exit(0))
    .catch((err) => {
      console.error(`[scrub] fatal: ${err.message}`);
      process.exit(1);
    });
}
