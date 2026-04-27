#!/usr/bin/env node
import { pathToFileURL } from "url";
import fs from "fs-extra";
import { resolveScraperPaths, seedPostsFileIfMissing } from "./paths.js";
import { listTargets, runCdpCommand, selectMarketEarTab } from "./cdp.js";
import { buildExtractionExpression, parsePayload } from "./extract.js";
import { createImageDownloader, hydrateLocalImages } from "./media.js";
import { loadExistingPosts, mergePosts, persistPosts } from "./store.js";
import { runForever } from "./scheduler.js";

const INTERVAL_MS = 2 * 60 * 1000;

export function createScraper(overrides = {}) {
  const paths = resolveScraperPaths(overrides);
  const downloader = createImageDownloader({ mediaDir: paths.mediaDir });

  async function scrapeOnce() {
    const cycleStart = Date.now();

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

    if (!changed && !imagesUpdated) {
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
      `[newsfeed] cycle ok N=${merged.length} changed=${changed} imagesUpdated=${imagesUpdated} ms=${Date.now() - cycleStart}`,
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
