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
      // CI chromium inherits http(s)_proxy from the environment and routes even
      // loopback through it, yielding ERR_NAME_NOT_RESOLVED for 127.0.0.1 (curl
      // bypasses the proxy, which is why the wait-loop passes). Force a direct
      // connection. No-op locally where no proxy is set.
      args: ["--no-proxy-server", "--proxy-bypass-list=*"],
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
