import path from "path";
import { fileURLToPath } from "url";
import fs from "fs-extra";
import { chromium } from "playwright";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const DEFAULT_STORAGE_PATH = path.join(PROJECT_ROOT, "data", "newsfeed-storage.json");
const DEFAULT_USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36";

function isHeadless() {
  const raw = process.env.RADON_NEWSFEED_HEADLESS;
  if (raw === undefined || raw === null || raw === "") return true;
  return raw !== "0" && raw.toLowerCase() !== "false";
}

async function readStorageStateIfPresent(storageStatePath) {
  if (!storageStatePath) return undefined;
  if (!(await fs.pathExists(storageStatePath))) return undefined;
  return storageStatePath;
}

export async function createBrowser({
  storageStatePath = DEFAULT_STORAGE_PATH,
  launcher = chromium,
  headless = isHeadless(),
  viewport = { width: 1440, height: 900 },
  userAgent = DEFAULT_USER_AGENT,
} = {}) {
  await fs.ensureDir(path.dirname(storageStatePath));

  const browser = await launcher.launch({ headless });
  const storageState = await readStorageStateIfPresent(storageStatePath);
  const context = await browser.newContext({
    viewport,
    userAgent,
    storageState,
  });
  const page = await context.newPage();

  async function exportCookies(urls) {
    const list = await context.cookies(urls);
    return list
      .filter((cookie) => cookie && typeof cookie.name === "string" && typeof cookie.value === "string")
      .map((cookie) => `${cookie.name}=${cookie.value}`)
      .join("; ");
  }

  async function persistStorageState() {
    await context.storageState({ path: storageStatePath });
  }

  async function close() {
    try {
      await context.close();
    } catch {
      /* ignore */
    }
    try {
      await browser.close();
    } catch {
      /* ignore */
    }
  }

  return {
    browser,
    context,
    page,
    storageStatePath,
    exportCookies,
    persistStorageState,
    close,
  };
}

export const NEWSFEED_DEFAULT_STORAGE_PATH = DEFAULT_STORAGE_PATH;
