import { test, expect } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";
import { resolvedColor } from "./performance-fixtures";

// ─── Shared mock data ─────────────────────────────────────────────────────────

const PORTFOLIO = {
  bankroll: 100000,
  peak_value: 100000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [],
  exposure: {},
  violations: [],
};

const ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [
    {
      execId: "exec-aapl-1",
      symbol: "AAPL",
      contract: { symbol: "AAPL", secType: "STK", currency: "USD", exchange: "SMART" },
      side: "BOT",
      quantity: 10,
      avgPrice: 270.0,
      commission: -1.0,
      realizedPNL: 500,
      time: new Date().toISOString(),
      exchange: "NASDAQ",
    },
  ],
  open_count: 0,
  executed_count: 1,
};

/** Stub all API routes so the app renders without a live backend. */
async function stubRoutes(page: import("@playwright/test").Page) {
  await page.route("**/api/portfolio**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/orders", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
  );
  await page.route("**/api/prices", (r) => r.abort());
  await page.route("**/api/regime", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 10, cri: { score: 10 } }) }),
  );
  await page.route("**/api/ib-status", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
  );
  await page.route("**/api/blotter", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  await page.route("**/api/ticker/**", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        uw_info: { name: "Apple Inc.", sector: "Technology" },
        stock_state: {},
        profile: {},
        stats: {},
      }),
    }),
  );
}

// ─── Tests ────────────────────────────────────────────────────────────────────

test("chart tooltip dark mode: html[data-theme] is 'dark' by default", async ({ page }) => {
  await installClearFixtures(page);
  await stubRoutes(page);

  // Force dark mode via localStorage before navigating
  await page.addInitScript(() => {
    localStorage.setItem("theme", "dark");
  });

  await page.goto("/orders");

  // Confirm the shell applied the dark theme to the document root
  const htmlTheme = await page.getAttribute("html", "data-theme");
  expect(htmlTheme).toBe("dark");
});

test("chart tooltip light mode: toggling theme changes html[data-theme] to 'light'", async ({ page }) => {
  await installClearFixtures(page);
  await stubRoutes(page);

  // Start in dark mode
  await page.addInitScript(() => {
    localStorage.setItem("theme", "dark");
  });

  await page.goto("/orders");

  // Find and click the theme toggle button in the header
  const themeToggle = page.locator('[aria-label="Toggle theme"], button:has-text("Light"), button:has-text("Dark"), .theme-toggle').first();
  await themeToggle.waitFor({ timeout: 5_000 });
  await themeToggle.click();

  // After toggle, html[data-theme] must be "light"
  const htmlTheme = await page.getAttribute("html", "data-theme");
  expect(htmlTheme).toBe("light");

  // Verify localStorage was updated
  const stored = await page.evaluate(() => localStorage.getItem("theme"));
  expect(stored).toBe("light");
});

test("chart tooltip: ticker detail page survives theme toggle without crash", async ({ page }) => {
  await installClearFixtures(page);
  await stubRoutes(page);

  await page.addInitScript(() => {
    localStorage.setItem("theme", "dark");
  });

  await page.goto("/AAPL?tab=book");

  const detail = page.locator(".ticker-detail-page");
  await detail.waitFor({ timeout: 5_000 });

  // Confirm page is in dark mode
  expect(await page.getAttribute("html", "data-theme")).toBe("dark");

  // Toggle theme while page is open
  const themeToggle = page.locator('[aria-label="Toggle theme"], button:has-text("Light"), button:has-text("Dark"), .theme-toggle').first();
  await themeToggle.waitFor({ timeout: 5_000 });
  await themeToggle.click();

  // Page must still be visible — no crash from re-render
  await expect(detail).toBeVisible();

  // Theme must now be light
  expect(await page.getAttribute("html", "data-theme")).toBe("light");
});

test("current instrument book surface follows theme tokens", async ({ page }) => {
  await installClearFixtures(page);
  await page.addInitScript(() => localStorage.setItem('theme', 'dark'));
  await page.goto('/AAPL?tab=book');
  const book = page.locator('.ticker-detail-page');
  await expect(book).toBeVisible();
  const dark = await book.evaluate(el => getComputedStyle(el).color);
  await page.getByLabel('Toggle theme').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(book).toBeVisible();
  const light = await book.evaluate(el => getComputedStyle(el).color);
  expect(light).not.toBe(dark);
  expect(light).toBe(await resolvedColor(page, '--text-primary'));
});
