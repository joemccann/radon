#!/usr/bin/env node
import path from "path";
import { fileURLToPath, pathToFileURL } from "url";
import fs from "fs-extra";
import dotenv from "dotenv";
import { resolveScraperPaths, seedPostsFileIfMissing } from "./paths.js";
import {
  fetchCookieHeader,
  listTargets,
  runCdpCommand,
  selectMarketEarTab,
  setActivePage,
} from "./cdp.js";
import { buildExtractionExpression, parsePayload } from "./extract.js";
import { createImageDownloader, hydrateLocalImages } from "./media.js";
import { loadExistingPosts, mergePosts, persistPosts } from "./store.js";
import { pushMedia } from "./push_media.js";
import { runForever } from "./scheduler.js";
import { appendTaxonomy, recordServiceHealth, upsertPosts } from "../db/writer.js";
import { createTagger } from "./tagger.js";
import { createVisionTagger, hydrateTagsDual } from "./vision_tagger.js";
import { appendTagsToTaxonomy, loadTaxonomy } from "./taxonomy.js";
import { createBrowser, NEWSFEED_DEFAULT_STORAGE_PATH } from "./browser.js";
import { ensureAuthenticated } from "./auth.js";

// Concurrently spawns this process without env inheritance from `next dev`,
// so neither CEREBRAS_API_KEY nor ANTHROPIC_API_KEY are present. Load web/.env
// (and root .env for completeness) up-front so both taggers can construct.
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.resolve(__dirname, "../../.env") });
dotenv.config({ path: path.resolve(__dirname, "../../web/.env") });

const INTERVAL_MS = 2 * 60 * 1000;
const REAUTH_INTERVAL_MS = 6 * 60 * 60 * 1000; // 6h — refresh storage state before cookies expire
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
  const storageStatePath = overrides.storageStatePath || NEWSFEED_DEFAULT_STORAGE_PATH;

  let browserHandle = null;
  let lastAuthAt = 0;

  async function getBrowser() {
    if (!browserHandle) {
      browserHandle = await createBrowser({ storageStatePath });
      setActivePage(browserHandle.page, browserHandle.context);
    }
    return browserHandle;
  }

  async function closeBrowser() {
    if (browserHandle) {
      try {
        await browserHandle.close();
      } catch {
        /* ignore */
      }
      browserHandle = null;
      setActivePage(null, null);
    }
  }

  async function authenticateIfNeeded({ force = false } = {}) {
    const handle = await getBrowser();
    const elapsed = Date.now() - lastAuthAt;
    if (!force && lastAuthAt > 0 && elapsed < REAUTH_INTERVAL_MS) {
      return;
    }
    await ensureAuthenticated({
      context: handle.context,
      page: handle.page,
      persistStorageState: handle.persistStorageState,
    });
    lastAuthAt = Date.now();
  }

  const getCookieHeader = async () => {
    if (!browserHandle) return "";
    try {
      return await fetchCookieHeader(null, COOKIE_URLS);
    } catch (err) {
      console.warn(`[newsfeed] cookie lookup failed: ${err.message}`);
      return "";
    }
  };

  const downloader = createImageDownloader({ mediaDir: paths.mediaDir, getCookieHeader });

  async function scrapeOnce() {
    const cycleStart = Date.now();
    const cycleStartIso = new Date(cycleStart).toISOString();

    await authenticateIfNeeded();
    const handle = await getBrowser();

    // Re-navigate every cycle so we always have fresh DOM (prior cycle could
    // have left the page in any state).
    await handle.page.goto("https://themarketear.com/newsfeed", {
      waitUntil: "domcontentloaded",
      timeout: 30_000,
    });
    if (typeof handle.page.waitForLoadState === "function") {
      await handle.page
        .waitForLoadState("networkidle", { timeout: 15_000 })
        .catch(() => {});
    }

    const pages = await listTargets();
    const target = selectMarketEarTab(pages);
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
              try {
                await appendTaxonomy(additions);
              } catch (err) {
                console.warn(`[newsfeed] db taxonomy append non-fatal: ${err.message}`);
              }
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

    let pushedToHetzner = 0;
    if (imagesUpdated) {
      const pushResult = await pushMedia({ local: `${paths.mediaDir}/` });
      if (pushResult.ok) {
        pushedToHetzner = pushResult.transferred ?? 0;
      } else {
        console.warn(`[newsfeed] media push non-fatal: ${pushResult.reason}`);
      }
    }

    let dbWritten = 0;
    try {
      await upsertPosts(merged);
      dbWritten = merged.length;
      await recordServiceHealth("newsfeed-scraper", "ok", {
        startedAt: cycleStartIso,
        finishedAt: new Date().toISOString(),
      });
    } catch (err) {
      console.warn(`[newsfeed] db dual-write non-fatal: ${err.message}`);
      try {
        await recordServiceHealth("newsfeed-scraper", "error", {
          startedAt: cycleStartIso,
          finishedAt: new Date().toISOString(),
          error: { message: err.message },
        });
      } catch (_inner) {
        /* ignore — health write is best-effort */
      }
    }

    console.info(
      `[newsfeed] cycle ok N=${merged.length} changed=${changed} imagesUpdated=${imagesUpdated} pushedToHetzner=${pushedToHetzner} tagsUpdated=${tagsUpdated} newTags=${newTagsAdded} dbWritten=${dbWritten} ms=${Date.now() - cycleStart}`,
    );
    return { changed: true, count: merged.length };
  }

  return { paths, scrapeOnce, closeBrowser, authenticateIfNeeded };
}

export async function run({ intervalMs = INTERVAL_MS, signal, ...overrides } = {}) {
  const { paths, scrapeOnce, closeBrowser } = createScraper(overrides);

  await fs.ensureDir(paths.dataDir);
  await fs.ensureDir(paths.archiveDir);
  await fs.ensureDir(paths.mediaDir);
  await seedPostsFileIfMissing(paths);

  console.info(`[newsfeed] starting — polling every ${Math.round(intervalMs / 1000)}s`);

  try {
    await runForever({
      intervalMs,
      scrapeOnce,
      signal,
      onCycleError: (err) => console.error(`[newsfeed] cycle failed: ${err.message}`),
    });
  } finally {
    await closeBrowser();
  }

  console.info("[newsfeed] stopped");
}

export async function scrapeOnce(overrides = {}) {
  const { scrapeOnce: runOnce, closeBrowser } = createScraper(overrides);
  try {
    return await runOnce();
  } finally {
    await closeBrowser();
  }
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
  const argv = process.argv.slice(2);
  const onceMode = argv.includes("--once");

  if (onceMode) {
    scrapeOnce()
      .then((result) => {
        console.info(`[newsfeed] --once complete: changed=${result.changed} count=${result.count}`);
        process.exit(0);
      })
      .catch((err) => {
        console.error(`[newsfeed] fatal: ${err.message}`);
        process.exit(1);
      });
  } else {
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
}
