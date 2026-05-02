#!/usr/bin/env node
// Retroactively tag posts in posts.json. Default mode tags posts with <3 tags.
// --retag re-tags every post (use after refining the prompt or naming rules).
// Novel tags returned by the model are auto-appended to data/tag_taxonomy.json.
//
// Routing: posts with a local image use the Anthropic vision tagger
// (claude-haiku-4-5); text-only posts use the Cerebras text tagger. Throttle
// targets the more conservative Cerebras 30 rpm limit.

import path from "path";
import { fileURLToPath } from "url";
import dotenv from "dotenv";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.resolve(__dirname, "../../.env") });
dotenv.config({ path: path.resolve(__dirname, "../../web/.env") });

import { resolveScraperPaths } from "./paths.js";
import { loadExistingPosts, persistPosts } from "./store.js";
import { createTagger } from "./tagger.js";
import { createVisionTagger, hydrateTagsDual } from "./vision_tagger.js";
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
      "Dual-classifies every post in web/public/data/posts.json:",
      "  - tags_text   : Cerebras text tagger (gpt-oss-120b → qwen-3-235b fallback)",
      "  - tags_vision : Anthropic Claude vision tagger (claude-haiku-4-5)",
      "  - tags        : union of the two (deduped, dashboard-facing)",
      "",
      "Cursor: a post is skipped only when both classifications are present",
      "(vision is N/A for posts with no local image). --retag re-runs both",
      "classifiers on every post regardless of state.",
      "",
      "Flags:",
      "  --retag   Re-tag every post (vision + text).",
      "  --help    Show this help.",
      "",
      `Throttle: ${THROTTLE_MS}ms between posts (~${Math.floor(60_000 / THROTTLE_MS)} posts/min). Per-post text+vision run in parallel.`,
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
  console.log(
    `[backfill] mode: ${force ? "RETAG (every post, both classifiers)" : "default (skip posts with both classifications complete)"}`,
  );

  const initialTaxonomy = await loadTaxonomy(paths.projectRoot);
  console.log(`[backfill] starting taxonomy size: ${initialTaxonomy.tags.length}`);

  const taxonomyFn = async () => (await loadTaxonomy(paths.projectRoot)).tags;

  let textTagger = null;
  try {
    textTagger = createTagger({ getTaxonomySnapshot: taxonomyFn });
  } catch (err) {
    console.warn(`[backfill] text tagger disabled: ${err.message}`);
  }

  let visionTagger = null;
  try {
    visionTagger = createVisionTagger({
      publicRoot: paths.publicRoot,
      getTaxonomySnapshot: taxonomyFn,
    });
  } catch (err) {
    console.warn(`[backfill] vision tagger disabled: ${err.message}`);
  }

  if (!textTagger && !visionTagger) {
    throw new Error("no taggers available — set CEREBRAS_API_KEY and/or ANTHROPIC_API_KEY");
  }

  console.log(`[backfill] taggers: text=${textTagger ? "ON" : "off"} vision=${visionTagger ? "ON" : "off"}`);

  const posts = await loadExistingPosts(paths.postsFile);
  console.log(`[backfill] loaded ${posts.length} posts`);

  const needsWork = (p) => {
    const hasImage = Array.isArray(p.images) && p.images.length > 0;
    const textComplete = Array.isArray(p.tags_text) && p.tags_text.length === 3;
    const visionComplete =
      !hasImage || (Array.isArray(p.tags_vision) && p.tags_vision.length === 3);
    return !(textComplete && visionComplete);
  };

  const start = Date.now();
  const target = force ? posts.length : posts.filter(needsWork).length;
  console.log(`[backfill] will tag ${target} post(s); throttling ${THROTTLE_MS}ms between posts`);

  let totalNewTags = 0;

  const updated = await hydrateTagsDual(posts, {
    textTagger,
    visionTagger,
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
    console.log("[backfill] nothing to update — every post already has both classifications");
    return;
  }

  await persistPosts(posts, {
    dataDir: paths.dataDir,
    archiveDir: paths.archiveDir,
    mediaDir: paths.mediaDir,
    postsFile: paths.postsFile,
  });

  const remaining = posts.filter(needsWork).length;
  const elapsed = ((Date.now() - start) / 1000).toFixed(1);
  const finalTaxonomy = await loadTaxonomy(paths.projectRoot);
  console.log(
    `[backfill] done in ${elapsed}s — ${remaining} post(s) still incomplete; taxonomy ${initialTaxonomy.tags.length} → ${finalTaxonomy.tags.length} (+${totalNewTags})`,
  );
}

main().catch((err) => {
  console.error(`[backfill] fatal: ${err.message}`);
  process.exit(1);
});
