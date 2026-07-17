import { expect, test, type Page } from "@playwright/test";

test.use({ serviceWorkers: "block" });

const NOW = "2026-07-09T15:00:00Z";

const PORTFOLIO = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: NOW,
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [],
};

const ORDERS = {
  last_sync: NOW,
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

async function stubApis(page: Page) {
  await page.route("**/api/watchlist", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        watchlist: [
          { id: "w1", symbol: "AAPL", sector: "Technology", added_at: NOW },
          { id: "w2", symbol: "MSFT", sector: "Technology", added_at: NOW },
        ],
      }),
    });
  });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/watchlist") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          watchlist: [
            { id: "w1", symbol: "AAPL", sector: "Technology", added_at: NOW },
            { id: "w2", symbol: "MSFT", sector: "Technology", added_at: NOW },
          ],
        }),
      });
      return;
    }

    if (path === "/api/portfolio") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) });
      return;
    }

    if (path === "/api/orders") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) });
      return;
    }

    if (path === "/api/profile") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ username: "Operator", avatar_url: null }) });
      return;
    }

    if (path === "/api/service-health") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ services: [] }) });
      return;
    }

    if (path === "/api/ticker/info") {
      const ticker = url.searchParams.get("ticker") ?? "AAPL";
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ticker,
          uw_info: { name: `${ticker} Inc.`, sector: "Technology", description: `${ticker} profile` },
          stock_state: { open: 100, high: 105, low: 99, close: 101, prev_close: 100, volume: 1_000_000 },
          profile: {},
          stats: {},
        }),
      });
      return;
    }

    if (path === "/api/ticker/news") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
      return;
    }

    if (path === "/api/ticker/ratings") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ratings: [] }) });
      return;
    }

    if (path === "/api/ticker/seasonality") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ months: [] }) });
      return;
    }

    if (path === "/api/options/expirations") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ symbol: url.searchParams.get("symbol") ?? "AAPL", expirations: ["20260320"] }),
      });
      return;
    }

    if (path === "/api/options/chain") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          symbol: url.searchParams.get("symbol") ?? "AAPL",
          expiry: "20260320",
          exchange: "SMART",
          strikes: [190, 195, 200, 205, 210],
          multiplier: "100",
        }),
      });
      return;
    }

    if (path === "/api/previous-close") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ closes: {} }) });
      return;
    }

    if (path === "/api/flex-token") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ remaining: 240 }) });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });

}

test.describe("Watchlist page", () => {
  test("lists symbols with no inline detail and navigates to the cockpit on click", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await stubApis(page);

    await page.goto("/watchlist");
    await expect(page.getByTestId("watchlist-page")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Watchlist" })).toBeVisible();
    await expect(page.getByTestId("watchlist-row-AAPL")).toBeVisible();
    await expect(page.getByTestId("watchlist-row-MSFT")).toBeVisible();
    await expect(page.getByTestId("watchlist-detail")).toHaveCount(0);
    await expect(page.getByText("INLINE DETAIL")).toHaveCount(0);

    await page.getByRole("button", { name: "Open MSFT instrument cockpit" }).click();

    await expect(page).toHaveURL(/\/MSFT$/);
    await expect(page.locator(".cockpit-head")).toContainText("MSFT", { timeout: 10000 });
  });
});
