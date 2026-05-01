#!/usr/bin/env node
// Retroactively tag posts in posts.json. Default: only posts with fewer than 3 valid tags.
// --retag: re-tag every post regardless. Run while the live scraper is paused for cleanest result.

import path from "path";
import fs from "fs-extra";
import { resolveScraperPaths } from "./paths.js";
import { loadExistingPosts, persistPosts } from "./store.js";
import { createTagger, hydrateTags } from "./tagger.js";

const FREE_TIER_PER_MIN = 30;
const SAFETY_MARGIN = 5; // stay below the limit
const THROTTLE_MS = Math.ceil((60_000 / (FREE_TIER_PER_MIN - SAFETY_MARGIN)));

function parseArgs(argv) {
  const flags = new Set();
  for (const arg of argv.slice(2)) {
    if (arg.startsWith("--")) flags.add(arg.slice(2));
  }
  return { force: flags.has("retag"), help: flags.has("help") };
}

function printHelp() {
  console.log(
    [
      "Usage: node scripts/newsfeed/backfill_tags.js [--retag]",
      "",
      "Tags posts in web/public/data/posts.json using Cerebras (gpt-oss-120b →",
      "llama-3.3-70b fallback). Default mode skips posts already carrying ≥3",
      "valid tags. --retag forces every post to be re-tagged.",
      "",
      "Flags:",
      "  --retag   Re-tag every post (use after expanding data/tag_taxonomy.json).",
      "  --help    Show this help.",
      "",
      `Throttle: ${THROTTLE_MS}ms between requests (~${Math.floor(60_000 / THROTTLE_MS)} req/min, well under free-tier 30 rpm).`,
    ].join("\n"),
  );
}

async function loadTaxonomy(projectRoot) {
  const file = path.join(projectRoot, "data", "tag_taxonomy.json");
  const raw = await fs.readFile(file, "utf8");
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed?.tags) || parsed.tags.length === 0) {
    throw new Error(`taxonomy at ${file} is empty`);
  }
  return parsed.tags;
}

async function main() {
  const { force, help } = parseArgs(process.argv);
  if (help) {
    printHelp();
    return;
  }

  const paths = resolveScraperPaths();
  console.log(`[backfill] posts file: ${paths.postsFile}`);
  console.log(`[backfill] mode: ${force ? "RETAG (every post)" : "default (only posts with <3 tags)"}`);

  const taxonomy = await loadTaxonomy(paths.projectRoot);
  console.log(`[backfill] taxonomy size: ${taxonomy.length}`);

  const tagger = createTagger({ taxonomy });
  const posts = await loadExistingPosts(paths.postsFile);
  console.log(`[backfill] loaded ${posts.length} posts`);

  const start = Date.now();
  const target = force
    ? posts.length
    : posts.filter((p) => !Array.isArray(p.tags) || p.tags.length < 3).length;
  console.log(`[backfill] will tag ${target} post(s); throttling ${THROTTLE_MS}ms between requests`);

  const updated = await hydrateTags(posts, tagger, { force, throttleMs: THROTTLE_MS });
  if (!updated) {
    console.log("[backfill] nothing to update — all posts already meet the tag threshold");
    return;
  }

  await persistPosts(posts, {
    dataDir: paths.dataDir,
    archiveDir: paths.archiveDir,
    mediaDir: paths.mediaDir,
    postsFile: paths.postsFile,
  });

  const remaining = posts.filter((p) => !Array.isArray(p.tags) || p.tags.length < 3).length;
  const elapsed = ((Date.now() - start) / 1000).toFixed(1);
  console.log(`[backfill] done in ${elapsed}s — ${remaining} post(s) still under-tagged (will retry next cycle)`);
}

main().catch((err) => {
  console.error(`[backfill] fatal: ${err.message}`);
  process.exit(1);
});
