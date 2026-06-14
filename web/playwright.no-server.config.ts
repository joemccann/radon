import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.PLAYWRIGHT_PORT ? Number(process.env.PLAYWRIGHT_PORT) : 3000;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    // 127.0.0.1, not "localhost": headless chromium on GitHub runners can fail
    // DNS resolution of the hostname (net::ERR_NAME_NOT_RESOLVED) while the IP
    // always works. Relative goto()s in specs resolve against this baseURL.
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "on-first-retry",
    launchOptions: {
      // CI chromium returns ERR_NAME_NOT_RESOLVED for loopback (even the literal
      // IP 127.0.0.1) while curl from the same shell gets 200 and no proxy is
      // set. Canonical CI-chromium hardening + forcing the system resolver
      // (AsyncDns off) so a valid IPv4 literal can't fail name resolution.
      args: [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-features=AsyncDns",
      ],
    },
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
});
