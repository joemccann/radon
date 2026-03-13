import { expect, test } from "@playwright/test";
import { REGIME_SYNC_CONFIG } from "../lib/useRegime";

const CLOSED_SNAPSHOT_EARLY = {
  scan_time: "2026-03-12T13:02:06-07:00",
  market_open: false,
  date: "2026-03-12",
  vix: 24.23,
};

const CLOSED_SNAPSHOT_SETTLED = {
  scan_time: "2026-03-12T13:03:13-07:00",
  market_open: false,
  date: "2026-03-12",
  vix: 26.72,
};

test.describe("/regime close refresh contract", () => {
  test("follows the cache with GET refreshes so a closed page can roll to the settled snapshot", async ({ page }) => {
    const harnessConfig = {
      interval: REGIME_SYNC_CONFIG.interval ?? 0,
      hasPost: REGIME_SYNC_CONFIG.hasPost ?? true,
    };

    await page.setContent(`
      <div data-testid="market-closed-indicator">MARKET CLOSED - END OF DAY VALUES</div>
      <div data-testid="strip-vix"><span class="regime-strip-value">---</span></div>
      <div class="regime-hero-timestamp">---</div>
      <script>
        const config = ${JSON.stringify(harnessConfig)};
        const early = ${JSON.stringify(CLOSED_SNAPSHOT_EARLY)};
        const settled = ${JSON.stringify(CLOSED_SNAPSHOT_SETTLED)};
        const methods = [];
        let getCount = 0;
        let didInitialSync = false;

        window.__regimeMethods = methods;

        window.fetch = async (_url, options = {}) => {
          const method = options.method || "GET";
          methods.push(method);

          if (method === "POST") {
            return {
              ok: true,
              async json() {
                return early;
              },
            };
          }

          getCount += 1;
          const payload = getCount === 1 ? early : settled;
          return {
            ok: true,
            async json() {
              return payload;
            },
          };
        };

        function render(payload) {
          document.querySelector(".regime-strip-value").textContent = payload.vix.toFixed(2);
          document.querySelector(".regime-hero-timestamp").textContent =
            new Date(payload.scan_time).toLocaleTimeString("en-US");
        }

        async function triggerSync() {
          const method = config.hasPost ? "POST" : "GET";
          const res = await window.fetch("/api/regime", { method });
          render(await res.json());
        }

        async function init() {
          const cached = await window.fetch("/api/regime", { method: "GET" });
          render(await cached.json());

          if (!didInitialSync) {
            didInitialSync = true;
            await triggerSync();
          }

          if (config.interval === 60000) {
            window.setTimeout(() => {
              void triggerSync();
            }, 25);
          }
        }

        void init();
      </script>
    `);

    await expect(page.locator('[data-testid="market-closed-indicator"]')).toBeVisible();
    await expect(page.locator('[data-testid="strip-vix"] .regime-strip-value')).toHaveText("24.23");
    await expect(page.locator(".regime-hero-timestamp")).toContainText("1:02:06 PM");

    await expect(page.locator('[data-testid="strip-vix"] .regime-strip-value')).toHaveText("26.72");
    await expect(page.locator(".regime-hero-timestamp")).toContainText("1:03:13 PM");

    const methods = await page.evaluate<string[]>(() => (window as unknown as { __regimeMethods: string[] }).__regimeMethods);
    expect(methods).not.toContain("POST");
    expect(methods.filter((method) => method === "GET").length).toBeGreaterThan(1);
  });
});
