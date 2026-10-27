import path from "path";
import { fileURLToPath } from "url";
import fs from "fs-extra";
import { renewFirebaseSession } from "./firebaseSession.js";

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

// Free-tier visitors land on /newsfeed too — same URL, same article cards,
// but every body is replaced with this stub. URL-only auth checks pass
// silently, then the extractor scrapes paywall text into posts.json. Sniff
// the DOM to disambiguate. 2026-05-16: ~25 posts shipped paywalled before
// catching this; surfaced as "newsfeed broken — premium gone" by ops.
//
// `PAYWALL_STUB_MARKER` is the loose substring used by the auth DOM probe.
// `PAYWALL_FULL_MESSAGE` is the canonical full-string match used by the
// cycle-level runtime guard (cycle.js). The cycle uses the full string
// because it scans already-extracted post `content`, where false positives
// matter more than the auth probe's all-or-nothing branch.
export const PAYWALL_STUB_MARKER = "part of our Premium coverage";
export const PAYWALL_FULL_MESSAGE =
  "This article is part of our Premium coverage, please click here to login and access it or sign up for a Premium account.";

async function pageHasPremiumContent(page, { strict = false } = {}) {
  if (typeof page.evaluate !== "function") return !strict;
  try {
    return await page.evaluate((marker) => {
      const bodies = document.querySelectorAll("article.post .body .content");
      if (bodies.length === 0) return false; // no articles → not authenticated
      // Any post body that lacks the paywall marker proves the session is
      // premium-tier. We don't require ALL posts to be non-stub because
      // some archived items legitimately show the stub even to subscribers.
      for (const node of bodies) {
        const text = (node.textContent || "").trim();
        if (text && !text.includes(marker)) return true;
      }
      return false;
    }, PAYWALL_STUB_MARKER);
  } catch {
    return !strict; // Warm reuse retains its legacy tolerance; renewal requires proof.
  }
}

async function tryReachNewsfeed(page, { timeout, requirePremiumProof = false }) {
  await page.goto(NEWSFEED_URL, { waitUntil: "domcontentloaded", timeout });
  if (typeof page.waitForLoadState === "function") {
    await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
  }
  const url = typeof page.url === "function" ? page.url() : "";
  if (!isAuthenticatedNewsfeedUrl(url)) return false;
  return await pageHasPremiumContent(page, { strict: requirePremiumProof });
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
  renewSession = renewFirebaseSession,
} = {}) {
  if (!page || typeof page.goto !== "function") {
    throw new NewsfeedAuthError("ensureAuthenticated requires a Playwright page object.");
  }

  let renewedSession = false;
  try {
    renewedSession = await renewSession({ context, env });
  } catch {
    console.warn("[newsfeed] Firebase session renewal unavailable; checking existing session.");
  }

  const reuseable = await tryReachNewsfeed(page, {
    timeout: timeouts.navigation, requirePremiumProof: renewedSession,
  }).catch(() => false);
  if (reuseable) {
    // Refresh disk-side cookies on the warm-reuse path so a process restart
    // doesn't fall back onto stale storage state. Without this, the only
    // persistence trigger is the cold-login path — which the 6h re-auth gate
    // skips entirely once a session is established.
    await tryPersistStorageState(persistStorageState);
    return { authenticated: true, reusedSession: true, ...(renewedSession ? { renewedSession: true } : {}) };
  }

  if (renewedSession) {
    throw new NewsfeedAuthError("Renewed session did not provide premium newsfeed content.");
  }
  const creds = credentials || readCredentialsFromEnv(env);

  try {
    await runLoginFlow(page, { email: creds.email, password: creds.password, timeouts });
  } catch (err) {
    const screenshotPath = await captureDebugScreenshot(page, debugDir);
    const message = err instanceof Error ? err.message : String(err);
    throw new NewsfeedAuthError(`themarketear.com login failed: ${message}`, { screenshotPath });
  }

  await tryPersistStorageState(persistStorageState);

  return { authenticated: true, reusedSession: false };
}

async function tryPersistStorageState(persistStorageState) {
  if (typeof persistStorageState !== "function") return;
  try {
    await persistStorageState();
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.warn(`[newsfeed] storage state persist failed: ${message}`);
  }
}

export const NEWSFEED_AUTH_DEFAULTS = {
  url: NEWSFEED_URL,
  homepage: HOMEPAGE_URL,
  timeouts: DEFAULT_TIMEOUTS,
};
