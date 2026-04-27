#!/usr/bin/env node
// Backwards-compat shim. The scraper now lives at scripts/newsfeed/.
// Existing cron/launchd jobs pointing at this path keep working.
import { pathToFileURL } from "url";
import { run } from "./newsfeed/index.js";
export { createScraper, run, scrapeOnce } from "./newsfeed/index.js";

const isDirect = (() => {
  if (!process.argv[1]) return false;
  try {
    return pathToFileURL(process.argv[1]).href === import.meta.url;
  } catch {
    return false;
  }
})();

if (isDirect) {
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
