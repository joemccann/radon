import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "../site/e2e",
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3336",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "cd ../site && NEXT_DIST_DIR=.next-site-playwright npx next dev --hostname 127.0.0.1 -p 3336 --webpack",
    url: "http://127.0.0.1:3336",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
