import { expect, test, type Page } from "@playwright/test";

const payload = {
  scan_time: "2026-09-16T15:00:00Z",
  source: "Unusual Whales + Radon vol/skew feeds",
  universe: "fallback:ndx100",
  requested_tickers: ["AAPL", "INTC", "NVDA"],
  tickers_scanned: 3,
  candidates_found: 3,
  actionable_count: 2,
  results: [
    {
      ticker: "AAPL",
      verdict: "TOP_MR",
      spot: 212.4,
      rsi: 78,
      pct_b: 1.04,
      extension: "HIGH",
      iv_path: "falling",
      skew_path: "falling",
      suggested_structure: "put spread",
      gates: { technicals: true, iv: true, skew: true },
      errors: [],
    },
    {
      ticker: "INTC",
      verdict: "BOTTOM_MR",
      spot: 22.1,
      rsi: 24,
      pct_b: -0.05,
      extension: "LOW",
      iv_path: "flat",
      skew_path: "falling",
      suggested_structure: "call spread",
      gates: { technicals: true, iv: true, skew: true },
      errors: [],
    },
    {
      ticker: "NVDA",
      verdict: "BREAKOUT",
      spot: 181.4,
      rsi: 74,
      pct_b: 1.1,
      extension: "HIGH",
      iv_path: "rising",
      skew_path: "rising",
      suggested_structure: null,
      gates: { technicals: true, iv: true, skew: false },
      errors: [],
    },
  ],
};

async function stubApis(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/scanner/vol-skew-mr" || path === "/api/scanner/vol-skew-mr/scan") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
      return;
    }
    if (path === "/api/scanner") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ scan_time: "2026-09-16T15:00:00Z", tickers_scanned: 0, signals_found: 0, top_signals: [] }),
      });
      return;
    }
    if (path === "/api/portfolio") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ positions: [], account_summary: {}, exposure: {}, violations: [] }),
      });
      return;
    }
    if (path === "/api/orders") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 }),
      });
      return;
    }
    if (path === "/api/service-health") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ services: [] }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.use({ viewport: { width: 320, height: 568 } });

/**
 * Regression (2026-09-17): on an iPhone the Vol/Skew MR tooltip opened
 * upward and its top lines rendered under the status bar / Dynamic Island.
 * The popup treated `top: 0` as the top of the readable screen, ignoring
 * the safe-area inset the layout already publishes as `--safe-top`.
 */
test.describe("InfoTooltip safe area", () => {
  test("keeps the popup clear of the notch inset on a phone", async ({ page }) => {
    await stubApis(page);
    await page.goto("/scanner?mode=vol-skew-mr");
    await expect(page.getByTestId("vol-skew-mr-section")).toBeVisible();

    const inset = 240;
    await page.evaluate((px) => {
      document.documentElement.style.setProperty("--safe-top", `${px}px`);
    }, inset);

    await page.getByTestId("vol-skew-mr-title-tooltip").hover();
    const popup = page.getByTestId("vol-skew-mr-title-tooltip-content");
    await expect(popup).toBeVisible();

    const box = await popup.evaluate((el) => {
      const rect = el.getBoundingClientRect();
      return { top: rect.top, left: rect.left, right: rect.right, bottom: rect.bottom };
    });

    expect(box.top).toBeGreaterThanOrEqual(inset + 8);
    expect(box.left).toBeGreaterThanOrEqual(8);
    expect(box.right).toBeLessThanOrEqual(320 - 8);
    expect(box.bottom).toBeLessThanOrEqual(568 - 8);
  });
});
