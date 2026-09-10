import { defineConfig, devices } from "@playwright/test";
import { randomUUID } from "node:crypto";

const PORT = process.env.PLAYWRIGHT_PORT ? Number(process.env.PLAYWRIGHT_PORT) : 3000;
// The config is evaluated in both the coordinator and worker processes. Seed
// the token into the coordinator environment so every child inherits the same
// high-entropy value; generating independently in each process makes the
// browser header differ from the web server's expected token.
const AUTHLESS_TEST_TOKEN = process.env.RADON_AUTHLESS_TEST_TOKEN ?? randomUUID();
process.env.RADON_AUTHLESS_TEST_TOKEN = AUTHLESS_TEST_TOKEN;
// The browser origin. Defaults to "localhost" (the app's canonical local host).
// CI sets PLAYWRIGHT_BASE_HOST=127.0.0.1: chromium's async DNS in the Playwright
// container resolves the "localhost" NAME flakily, and the literal loopback IP
// needs no resolution at all. 127.0.0.1 is in the middleware's LOCAL_HOSTS set,
// so the authless bypass (which reads the client host from the Host header)
// stays intact. This is unrelated to reachability — the server is always up;
// the historical "can't reach loopback" failures were a Clerk dev-browser
// handshake 307 to an unresolvable host, fixed in web/middleware.ts.
const HOST = process.env.PLAYWRIGHT_BASE_HOST ?? "localhost";

export default defineConfig({
  testDir: "./e2e",
  // Playwright's default testMatch also collects *.test.js — which pulled in
  // e2e/prices-performance.test.js, a standalone node script that self-spawns
  // `npm run dev` AT IMPORT TIME and crashed every run 7s in (TEST_AUDIT.md
  // T-001). Specs are *.spec.ts only.
  testIgnore: ["**/*.test.js"],
  fullyParallel: false,
  // One retry in CI absorbs container jitter (a lost click during a reactive
  // re-render, a slow first paint) without masking a real regression — a
  // deterministic failure still fails both attempts. Local stays at 0.
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: "list",
  use: {
    // These suites intercept broker/API transport with page.route. A running
    // service worker can own those requests before Playwright sees them.
    // SW behavior has dedicated unit contracts; opt in explicitly for SW E2E.
    serviceWorkers: "block",
    baseURL: `http://${HOST}:${PORT}`,
    extraHTTPHeaders: { "x-radon-authless-test": AUTHLESS_TEST_TOKEN },
    trace: "on-first-retry",
    // A cold `next dev` compiles each route on first navigation; in
    // the CI container that first hit can exceed the 30s default. A larger
    // ceiling only bites when a nav is genuinely slow, so it is harmless
    // locally where routes compile in a few seconds.
    navigationTimeout: 90_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile",
      testMatch: /mobile-.*\.spec\.ts$/,
      use: {
        ...devices["iPhone 15"],
        viewport: { width: 393, height: 852 },
      },
    },
  ],
  // Start Next.js before tests. Default is the dev server for fast local
  // iteration. CI overrides with a prebuilt `next start` via
  // PLAYWRIGHT_WEBSERVER_CMD: `next dev` (either bundler) never readies inside
  // the resource-constrained Playwright container — cold-compiling this app's
  // heavy routes on demand hangs there (runs 31268084987 / 31268824260 timed
  // out right after the middleware line). Production `next start` serves
  // prebuilt routes and readies in seconds — the same build+start pattern
  // perimeter-smoke already runs green in this workflow.
  webServer: {
    command: process.env.PLAYWRIGHT_WEBSERVER_CMD ?? `npx next dev --turbopack -p ${PORT}`,
    // Probe a STATIC asset for readiness, not "/". Under a production
    // `next start` the root route SSRs a Clerk `useSession` and can 500,
    // which would fail a readiness check on "/" even though every spec route
    // (/portfolio, /orders) renders fine. The manifest is served statically
    // (always 200) so readiness reflects "server up", nothing more.
    url: `http://${HOST}:${PORT}/manifest.webmanifest`,
    reuseExistingServer: true,
    timeout: 180_000,
    env: {
      ...process.env,
      RADON_AUTHLESS_TEST: "1",
      RADON_AUTHLESS_TEST_TOKEN: AUTHLESS_TEST_TOKEN,
      // Pin a Clerk publishable STUB (after the process.env spread, so it
      // always wins): whether specs exercise the realtime socket must not
      // depend on ambient NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY / web/.env.
      // pk_test_* is a non-secret instance identifier, never a credential.
      // Auth in e2e comes from the authless token above, not Clerk.
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY:
        "pk_test_cmFkb24tZTJlLXN0dWIuY2xlcmsuYWNjb3VudHMuZGV2JA",
    },
  },
});
