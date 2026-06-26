import { expect, test, type Page } from "@playwright/test";

const discoverPayload = {
  discovery_time: "2026-06-24T15:05:00Z",
  alerts_analyzed: 7,
  candidates_found: 1,
  candidates: [
    {
      ticker: "MSFT",
      score: 72.5,
      score_breakdown: {},
      alerts: 3,
      total_premium: 1_250_000,
      calls: 8,
      puts: 1,
      options_bias: "BULLISH",
      sweeps: 2,
      avg_vol_oi: 4.2,
      sector: "Technology",
      issue_type: "Common Stock",
      dp_direction: "ACCUMULATION",
      dp_strength: 64.1,
      dp_buy_ratio: 0.68,
      dp_sustained_days: 2,
      dp_total_prints: 19,
      confluence: true,
    },
  ],
};

async function stubApis(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === "/api/discover") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(discoverPayload) });
      return;
    }

    if (path === "/api/scanner") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ scan_time: "2026-06-24T15:00:00Z", tickers_scanned: 0, signals_found: 0, top_signals: [] }),
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

    if (path === "/api/profile") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ username: "Operator" }) });
      return;
    }

    if (path === "/api/watchlist") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
}

test.describe("Scanner discover mode", () => {
  test("legacy /discover redirects to the scanner discover mode", async ({ page }) => {
    await stubApis(page);

    await page.goto("/discover");

    await expect(page).toHaveURL(/\/scanner\?mode=discover$/);
    await expect(page.getByRole("tab", { name: "Discover" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText("Discovery Candidates")).toBeVisible();
    await expect(page.getByRole("button", { name: "View details for MSFT" })).toBeVisible();
  });

  test("discover mode renders mobile cards without horizontal overflow", async ({ page }) => {
    await page.setViewportSize({ width: 393, height: 852 });
    await stubApis(page);

    await page.goto("/scanner?mode=discover");

    await expect(page.getByRole("tab", { name: "Discover" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("mobile-discover-list")).toBeVisible();
    await expect(page.getByText("MSFT")).toBeVisible();

    const widths = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(widths.scrollWidth).toBeLessThanOrEqual(widths.clientWidth + 1);
  });
});
