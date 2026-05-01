#!/usr/bin/env node
// Retroactively tag posts in posts.json. Default mode tags posts with <3 tags.
// --retag re-tags every post (use after refining the prompt or naming rules).
// Novel tags returned by the model are auto-appended to data/tag_taxonomy.json.

import { resolveScraperPaths } from "./paths.js";
import { loadExistingPosts, persistPosts } from "./store.js";
import { createTagger, hydrateTags } from "./tagger.js";
import { appendTagsToTaxonomy, loadTaxonomy } from "./taxonomy.js";

const FREE_TIER_PER_MIN = 30;
const SAFETY_MARGIN = 5;
const THROTTLE_MS = Math.ceil(60_000 / (FREE_TIER_PER_MIN - SAFETY_MARGIN));

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
      "qwen-3-235b fallback). Open-vocabulary: novel tags returned by the model",
      "are auto-appended to data/tag_taxonomy.json.",
      "",
      "Default mode skips posts already carrying ≥3 tags. --retag forces every",
      "post to be re-tagged (use after the prompt or naming rules change).",
      "",
      "Flags:",
      "  --retag   Re-tag every post.",
      "  --help    Show this help.",
      "",
      `Throttle: ${THROTTLE_MS}ms between requests (~${Math.floor(60_000 / THROTTLE_MS)} req/min, well under free-tier 30 rpm).`,
    ].join("\n"),
  );
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

  const initialTaxonomy = await loadTaxonomy(paths.projectRoot);
  console.log(`[backfill] starting taxonomy size: ${initialTaxonomy.tags.length}`);

  const tagger = createTagger({
    getTaxonomySnapshot: async () => (await loadTaxonomy(paths.projectRoot)).tags,
  });

  const posts = await loadExistingPosts(paths.postsFile);
  console.log(`[backfill] loaded ${posts.length} posts`);

  const start = Date.now();
  const target = force
    ? posts.length
    : posts.filter((p) => !Array.isArray(p.tags) || p.tags.length < 3).length;
  console.log(`[backfill] will tag ${target} post(s); throttling ${THROTTLE_MS}ms between requests`);

  let totalNewTags = 0;

  const updated = await hydrateTags(posts, tagger, {
    force,
    throttleMs: THROTTLE_MS,
    onNewTags: async (tags) => {
      const additions = await appendTagsToTaxonomy(paths.projectRoot, tags);
      if (additions.length > 0) {
        totalNewTags += additions.length;
        console.log(`[backfill] taxonomy +${additions.length}: ${additions.join(", ")}`);
      }
    },
  });

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
  const finalTaxonomy = await loadTaxonomy(paths.projectRoot);
  console.log(
    `[backfill] done in ${elapsed}s — ${remaining} post(s) still under-tagged; taxonomy ${initialTaxonomy.tags.length} → ${finalTaxonomy.tags.length} (+${totalNewTags})`,
  );
}

main().catch((err) => {
  console.error(`[backfill] fatal: ${err.message}`);
  process.exit(1);
});
