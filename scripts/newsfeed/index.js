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
import { runScrapeCycle } from "./cycle.js";

// Concurrently spawns this process without env inheritance from `next dev`,
// so neither CEREBRAS_API_KEY nor ANTHROPIC_API_KEY are present. Load web/.env
// (and root .env for completeness) up-front so both taggers can construct.
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.resolve(__dirname, "../../.env") });
dotenv.config({ path: path.resolve(__dirname, "../../web/.env") });

const INTERVAL_MS = 2 * 60 * 1000;
const REAUTH_INTERVAL_MS = 6 * 60 * 60 * 1000; // 6h — refresh storage state before cookies expire
const PERSIST_INTERVAL_MS = 6 * 60 * 60 * 1000; // 6h — periodic persist gate, fires inside re-auth window
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
  let lastStoragePersistAt = 0;

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
      // Inside the re-auth window we still want disk-side cookies to track
      // the live browser context — otherwise a process restart after the
      // cookies on disk lapse falls back onto the fragile email/password
      // login flow. Persist on a separate gate without re-running the
      // ensureAuthenticated → tryReachNewsfeed navigation.
      await persistStorageStateIfDue(handle);
      return;
    }
    await ensureAuthenticated({
      context: handle.context,
      page: handle.page,
      persistStorageState: handle.persistStorageState,
    });
    lastAuthAt = Date.now();
    lastStoragePersistAt = Date.now();
  }

  // Force the NEXT call to authenticateIfNeeded() to run the full auth
  // flow (skipping the 6h re-auth gate). Used by the cycle when it
  // detects paywall stubs mid-window — themarketear.com flipped the
  // session to free-tier and we need a fresh login next cycle to recover.
  // We intentionally don't run ensureAuthenticated() synchronously here
  // because a single auth flow can take 30+ seconds and we don't want
  // to block the cycle's persist/upsert pipeline.
  function requestReauth() {
    lastAuthAt = 0;
  }

  async function persistStorageStateIfDue(handle) {
    if (typeof handle?.persistStorageState !== "function") return;
    const elapsed = Date.now() - lastStoragePersistAt;
    if (lastStoragePersistAt > 0 && elapsed < PERSIST_INTERVAL_MS) return;
    try {
      await handle.persistStorageState();
      lastStoragePersistAt = Date.now();
    } catch (err) {
      console.warn(`[newsfeed] periodic storage state persist failed: ${err.message}`);
    }
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

    return runScrapeCycle({
      cycleStartIso,
      loadExistingPosts,
      scrapePosts: async () => {
        const pages = await listTargets();
        const target = selectMarketEarTab(pages);
        const expression = buildExtractionExpression();
        const raw = await runCdpCommand("eval", target.targetId, expression);
        const payload = parsePayload(raw);
        if (!payload.ok) {
          const stage = payload.source === "dom" ? "extract" : "extract/parse";
          throw new Error(`[${stage}] ${payload.reason}`);
        }
        return { items: payload.items };
      },
      mergePosts,
      hydrateLocalImages: (posts) => hydrateLocalImages(posts, downloader),
      persistPosts,
      pushMedia,
      upsertPosts,
      hydrateTagsDual,
      recordServiceHealth,
      buildTextTagger: () => buildTextTaggerOrNull({ projectRoot: paths.projectRoot }),
      buildVisionTagger: () =>
        buildVisionTaggerOrNull({ projectRoot: paths.projectRoot, publicRoot: paths.publicRoot }),
      onNewTags: async (tags) => {
        const additions = await appendTagsToTaxonomy(paths.projectRoot, tags);
        if (additions.length > 0) {
          console.info(`[newsfeed] taxonomy +${additions.length}: ${additions.join(", ")}`);
          try {
            await appendTaxonomy(additions);
          } catch (err) {
            console.warn(`[newsfeed] db taxonomy append non-fatal: ${err.message}`);
          }
        }
        return additions;
      },
      requestReauth,
      paths,
    });
  }

  return { paths, scrapeOnce, closeBrowser, authenticateIfNeeded, requestReauth };
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
