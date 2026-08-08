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
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "on-first-retry",
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
  // Start Next.js dev server before tests
  webServer: {
    command: `npx next dev --turbopack -p ${PORT}`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: true,
    // Turbopack cold-start in the CI container exceeds 60s (run 31267802396
    // aborted here before any spec ran); a warm local start is ~5-10s, so a
    // larger ceiling only matters in CI and is harmless locally.
    timeout: 180_000,
    env: {
      ...process.env,
      RADON_AUTHLESS_TEST: "1",
      NEXT_PUBLIC_RADON_AUTHLESS_TEST: "1",
    },
  },
});
