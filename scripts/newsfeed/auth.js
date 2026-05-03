import path from "path";
import { fileURLToPath } from "url";
import fs from "fs-extra";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const DEFAULT_DEBUG_DIR = path.join(PROJECT_ROOT, "data");

const NEWSFEED_URL = "https://themarketear.com/newsfeed";
const HOMEPAGE_URL = "https://themarketear.com/";

const DEFAULT_TIMEOUTS = {
  navigation: 30_000,
  selector: 15_000,
  authResponse: 30_000,
};

export class NewsfeedAuthError extends Error {
  constructor(message, { screenshotPath } = {}) {
    super(message);
    this.name = "NewsfeedAuthError";
    if (screenshotPath) this.screenshotPath = screenshotPath;
  }
}

export function readCredentialsFromEnv(env = process.env) {
  const email = env.THEMARKETEAR_EMAIL?.trim();
  const password = env.THEMARKETEAR_PASSWORD?.trim();
  if (!email || !password) {
    throw new NewsfeedAuthError(
      "Missing THEMARKETEAR_EMAIL or THEMARKETEAR_PASSWORD environment variable.",
    );
  }
  return { email, password };
}

async function captureDebugScreenshot(page, debugDir) {
  if (!page || typeof page.screenshot !== "function") return null;
  try {
    await fs.ensureDir(debugDir);
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    const dest = path.join(debugDir, `newsfeed-debug-${ts}.png`);
    await page.screenshot({ path: dest, fullPage: false });
    return dest;
  } catch {
    return null;
  }
}

function isAuthenticatedNewsfeedUrl(url) {
  if (typeof url !== "string") return false;
  if (!url.includes("themarketear.com")) return false;
  if (!url.includes("/newsfeed")) return false;
  if (url.includes("/the-newsletter")) return false;
  if (url.includes("/login")) return false;
  return true;
}

async function tryReachNewsfeed(page, { timeout }) {
  await page.goto(NEWSFEED_URL, { waitUntil: "domcontentloaded", timeout });
  if (typeof page.waitForLoadState === "function") {
    await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
  }
  const url = typeof page.url === "function" ? page.url() : "";
  return isAuthenticatedNewsfeedUrl(url);
}

async function runLoginFlow(page, { email, password, timeouts }) {
  await page.goto(HOMEPAGE_URL, { waitUntil: "domcontentloaded", timeout: timeouts.navigation });
  if (typeof page.waitForLoadState === "function") {
    await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
  }

  const userIcon = page.locator("header button.menu-item").first();
  await userIcon.waitFor({ state: "visible", timeout: timeouts.selector });
  await userIcon.click({ force: true });

  await page.locator("#login-panel").waitFor({ state: "visible", timeout: timeouts.selector });

  const emailButton = page
    .locator("#login-panel")
    .getByRole("button", { name: /sign in with email/i });
  await emailButton.click();

  const emailInput = page
    .locator(
      '#login-panel input[type="email"], #login-panel input[name="email" i], #login-panel input',
    )
    .first();
  await emailInput.waitFor({ state: "visible", timeout: timeouts.selector });
  await emailInput.fill(email);

  const nextButton = page.locator("#login-panel").getByRole("button", { name: /^next$/i });
  await nextButton.click();

  const passwordInput = page.locator('#login-panel input[type="password"]').first();
  await passwordInput.waitFor({ state: "visible", timeout: timeouts.selector });
  await passwordInput.fill(password);

  const responseSignal = page
    .waitForResponse(
      (response) => /login-success/.test(response.url()) && response.status() === 200,
      { timeout: timeouts.authResponse },
    )
    .catch(() => null);

  await passwordInput.press("Enter");
  await responseSignal;

  await page.waitForURL(/\/newsfeed/, { timeout: timeouts.authResponse }).catch(() => null);
  if (typeof page.waitForLoadState === "function") {
    await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
  }

  const url = typeof page.url === "function" ? page.url() : "";
  if (!isAuthenticatedNewsfeedUrl(url)) {
    throw new NewsfeedAuthError(`Login flow finished but URL is unexpected: ${url}`);
  }
}

export async function ensureAuthenticated({
  context,
  page,
  credentials,
  persistStorageState,
  debugDir = DEFAULT_DEBUG_DIR,
  timeouts = DEFAULT_TIMEOUTS,
  env = process.env,
} = {}) {
  if (!page || typeof page.goto !== "function") {
    throw new NewsfeedAuthError("ensureAuthenticated requires a Playwright page object.");
  }

  const reuseable = await tryReachNewsfeed(page, { timeout: timeouts.navigation }).catch(() => false);
  if (reuseable) {
    return { authenticated: true, reusedSession: true };
  }

  const creds = credentials || readCredentialsFromEnv(env);

  try {
    await runLoginFlow(page, { email: creds.email, password: creds.password, timeouts });
  } catch (err) {
    const screenshotPath = await captureDebugScreenshot(page, debugDir);
    const message = err instanceof Error ? err.message : String(err);
    throw new NewsfeedAuthError(`themarketear.com login failed: ${message}`, { screenshotPath });
  }

  if (typeof persistStorageState === "function") {
    try {
      await persistStorageState();
    } catch (err) {
      console.warn(`[newsfeed] storage state persist failed: ${err.message}`);
    }
  } else if (context && typeof context.storageState === "function") {
    /* caller did not wire persistence — best-effort no-op */
  }

  return { authenticated: true, reusedSession: false };
}

export const NEWSFEED_AUTH_DEFAULTS = {
  url: NEWSFEED_URL,
  homepage: HOMEPAGE_URL,
  timeouts: DEFAULT_TIMEOUTS,
};
