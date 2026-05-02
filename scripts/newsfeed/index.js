#!/usr/bin/env node
import path from "path";
import { fileURLToPath, pathToFileURL } from "url";
import fs from "fs-extra";
import dotenv from "dotenv";
import { resolveScraperPaths, seedPostsFileIfMissing } from "./paths.js";
import { fetchCookieHeader, listTargets, runCdpCommand, selectMarketEarTab } from "./cdp.js";
import { buildExtractionExpression, parsePayload } from "./extract.js";
import { createImageDownloader, hydrateLocalImages } from "./media.js";
import { loadExistingPosts, mergePosts, persistPosts } from "./store.js";
import { runForever } from "./scheduler.js";
import { createTagger } from "./tagger.js";
import { createVisionTagger, hydrateTagsDual } from "./vision_tagger.js";
import { appendTagsToTaxonomy, loadTaxonomy } from "./taxonomy.js";

// Concurrently spawns this process without env inheritance from `next dev`,
// so neither CEREBRAS_API_KEY nor ANTHROPIC_API_KEY are present. Load web/.env
// (and root .env for completeness) up-front so both taggers can construct.
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.resolve(__dirname, "../../.env") });
dotenv.config({ path: path.resolve(__dirname, "../../web/.env") });

const INTERVAL_MS = 2 * 60 * 1000;
const COOKIE_URLS = ["https://themarketear.com"];

function buildTextTaggerOrNull({ projectRoot }) {
  try {
    return createTagger({
      getTaxonomySnapshot: async () => (await loadTaxonomy(projectRoot)).tags,
    });
  } catch (err) {
    console.warn(`[newsfeed] text tagger disabled: ${err.message}`);
    return null;
  }
}

function buildVisionTaggerOrNull({ projectRoot, publicRoot }) {
  try {
    return createVisionTagger({
      publicRoot,
      getTaxonomySnapshot: async () => (await loadTaxonomy(projectRoot)).tags,
    });
  } catch (err) {
    console.warn(`[newsfeed] vision tagger disabled: ${err.message}`);
    return null;
  }
}

export function createScraper(overrides = {}) {
  const paths = resolveScraperPaths(overrides);

  let activeTargetId = null;
  const getCookieHeader = async () => {
    if (!activeTargetId) return "";
    try {
      return await fetchCookieHeader(activeTargetId, COOKIE_URLS);
    } catch (err) {
      console.warn(`[newsfeed] cookie lookup failed: ${err.message}`);
      return "";
    }
  };

  const downloader = createImageDownloader({ mediaDir: paths.mediaDir, getCookieHeader });

  async function scrapeOnce() {
    const cycleStart = Date.now();

    const pages = await listTargets();
    const target = selectMarketEarTab(pages);
    activeTargetId = target.targetId;
    const expression = buildExtractionExpression();
    const raw = await runCdpCommand("eval", target.targetId, expression);

    const payload = parsePayload(raw);
    if (!payload.ok) {
      const stage = payload.source === "dom" ? "extract" : "extract/parse";
      throw new Error(`[${stage}] ${payload.reason}`);
    }

    if (payload.items.length === 0) {
      console.info(`[newsfeed] cycle empty ms=${Date.now() - cycleStart}`);
      return { changed: false, count: 0 };
    }

    const existing = await loadExistingPosts(paths.postsFile);
    const { merged, changed } = mergePosts(existing, payload.items);
    const imagesUpdated = await hydrateLocalImages(merged, downloader);

    let tagsUpdated = false;
    let newTagsAdded = 0;
    const textTagger = buildTextTaggerOrNull({ projectRoot: paths.projectRoot });
    const visionTagger = buildVisionTaggerOrNull({
      projectRoot: paths.projectRoot,
      publicRoot: paths.publicRoot,
    });
    if (textTagger || visionTagger) {
      try {
        tagsUpdated = await hydrateTagsDual(merged, {
          textTagger,
          visionTagger,
          onNewTags: async (tags) => {
            const additions = await appendTagsToTaxonomy(paths.projectRoot, tags);
            newTagsAdded += additions.length;
            if (additions.length > 0) {
              console.info(`[newsfeed] taxonomy +${additions.length}: ${additions.join(", ")}`);
            }
          },
        });
      } catch (err) {
        console.warn(`[newsfeed] tag hydration failed: ${err.message}`);
      }
    }

    if (!changed && !imagesUpdated && !tagsUpdated) {
      console.info(`[newsfeed] cycle nochange N=${merged.length} ms=${Date.now() - cycleStart}`);
      return { changed: false, count: merged.length };
    }

    await persistPosts(merged, {
      dataDir: paths.dataDir,
      archiveDir: paths.archiveDir,
      mediaDir: paths.mediaDir,
      postsFile: paths.postsFile,
    });

    console.info(
      `[newsfeed] cycle ok N=${merged.length} changed=${changed} imagesUpdated=${imagesUpdated} tagsUpdated=${tagsUpdated} newTags=${newTagsAdded} ms=${Date.now() - cycleStart}`,
    );
    return { changed: true, count: merged.length };
  }

  return { paths, scrapeOnce };
}

export async function run({ intervalMs = INTERVAL_MS, signal, ...overrides } = {}) {
  const { paths, scrapeOnce } = createScraper(overrides);

  await fs.ensureDir(paths.dataDir);
  await fs.ensureDir(paths.archiveDir);
  await fs.ensureDir(paths.mediaDir);
  await seedPostsFileIfMissing(paths);

  console.info(`[newsfeed] starting — polling every ${Math.round(intervalMs / 1000)}s`);

  await runForever({
    intervalMs,
    scrapeOnce,
    signal,
    onCycleError: (err) => console.error(`[newsfeed] cycle failed: ${err.message}`),
  });

  console.info("[newsfeed] stopped");
}

export async function scrapeOnce(overrides = {}) {
  const { scrapeOnce: runOnce } = createScraper(overrides);
  return runOnce();
}

function isDirectExecution() {
  if (!process.argv[1]) return false;
  try {
    return pathToFileURL(process.argv[1]).href === import.meta.url;
  } catch {
    return false;
  }
}

if (isDirectExecution()) {
  const controller = new AbortController();
  const shutdown = (signal) => {
    console.info(`[newsfeed] received ${signal} — shutting down`);
    controller.abort();
  };
  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("SIGTERM", () => shutdown("SIGTERM"));

  run({ signal: controller.signal }).catch((err) => {
    console.error(`[newsfeed] fatal: ${err.message}`);
    process.exit(1);
  });
}
