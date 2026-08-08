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
  // Start Next.js dev server before tests
  webServer: {
    command: `npx next dev --turbopack -p ${PORT}`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: true,
    timeout: 60_000,
    env: {
      ...process.env,
      RADON_AUTHLESS_TEST: "1",
      NEXT_PUBLIC_RADON_AUTHLESS_TEST: "1",
    },
  },
});
