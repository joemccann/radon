import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.PLAYWRIGHT_PORT ? Number(process.env.PLAYWRIGHT_PORT) : 3000;

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
    baseURL: `http://localhost:${PORT}`,
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
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: {
          // chromium's in-container async DNS resolver returns
          // ERR_NAME_NOT_RESOLVED for "localhost" (CI run 31263267789,
          // container mcr.microsoft.com/playwright) even though loopback
          // itself works there. Map the name straight to the loopback IP so
          // baseURL can stay "localhost" — keeping every localhost-keyed app
          // path and page.route mock identical to local runs. No-op locally,
          // where localhost already resolves to 127.0.0.1.
          args: ["--host-resolver-rules=MAP localhost 127.0.0.1"],
        },
      },
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
    url: `http://localhost:${PORT}`,
    reuseExistingServer: true,
    timeout: 180_000,
    env: {
      ...process.env,
      RADON_AUTHLESS_TEST: "1",
      NEXT_PUBLIC_RADON_AUTHLESS_TEST: "1",
    },
  },
});
