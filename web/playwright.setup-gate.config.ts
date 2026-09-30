import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.PLAYWRIGHT_SETUP_GATE_PORT ?? 3100);
const HOST = "127.0.0.1"; // pragma: allowlist secret

export default defineConfig({
  testDir: "./e2e-setup-gate",
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: "list",
  outputDir: "test-results-setup-gate",
  use: {
    serviceWorkers: "block",
    baseURL: `http://${HOST}:${PORT}`,
    trace: "on-first-retry",
    navigationTimeout: 90_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command:
      process.env.PLAYWRIGHT_SETUP_GATE_WEBSERVER_CMD
      ?? `npx next start -H ${HOST} -p ${PORT}`,
    url: `http://${HOST}:${PORT}/manifest.webmanifest`,
    reuseExistingServer: false,
    timeout: 180_000,
    env: {
      ...process.env,
      NEXT_DIST_DIR: ".next-setup-gate",
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "",
      CLERK_SECRET_KEY: "",
      RADON_SETUP_COMPLETE: "",
      RADON_AUTHLESS_TEST: "",
      RADON_AUTHLESS_TEST_TOKEN: "",
      NEXT_PUBLIC_RADON_AUTHLESS_TEST: "",
    },
  },
});
