/**
 * Desktop Close Position ticket: confirm summary + Gate 3 CRB is taller than
 * the act column. `.act-ticket` must be the scrollport so Place / Confirm
 * stay reachable. Reproduces the 2026-08-14 CBRS clip.
 */
import { expect, test, type Page } from "@playwright/test";

function stubApis(page: Page) {
  const emptyPortfolio = {
    bankroll: 100_000,
    peak_value: 100_000,
    last_sync: new Date().toISOString(),
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
  page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(emptyPortfolio) }),
  );
  page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        last_sync: new Date().toISOString(),
        open_orders: [],
        executed_orders: [],
        open_count: 0,
        executed_count: 0,
      }),
    }),
  );
  page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
  );
  page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  page.route("**/api/ticker/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ uw_info: { name: "Apple Inc." }, stock_state: {}, profile: {}, stats: {} }),
    }),
  );
  page.route("**/api/options/expirations*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ symbol: "AAPL", expirations: ["20260320"] }),
    }),
  );
  page.route("**/api/options/chain*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ symbol: "AAPL", expiry: "20260320", strikes: [200], multiplier: "100" }),
    }),
  );
  page.route("**/api/prices**", (route) => route.abort());
}

test("short desktop act column can scroll to Confirm Order", async ({ page }) => {
  test.setTimeout(120_000);
  stubApis(page);
  await page.setViewportSize({ width: 1280, height: 500 });
  await page.goto("/AAPL");

  const ticket = page.locator(".act-ticket");
  await ticket.waitFor({ timeout: 90_000 });

  const box = await ticket.evaluate((el) => {
    const s = getComputedStyle(el);
    return {
      overflowY: s.overflowY,
      flexShrink: s.flexShrink,
      minHeight: s.minHeight,
    };
  });
  expect(box.overflowY).toBe("auto");
  expect(Number(box.flexShrink)).toBeGreaterThan(0);
  expect(box.minHeight).toBe("0px");

  await ticket.locator("input.order-input").first().fill("35");
  await ticket.locator(".modify-price-input").first().fill("5");
  await ticket.getByRole("button", { name: "Place Order" }).click();

  const confirm = ticket.getByRole("button", { name: "Confirm Order" });
  await expect(confirm).toBeAttached();

  await ticket.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  await expect(confirm).toBeInViewport();
  await ticket.screenshot({ path: "test-results/act-ticket-scrolled.png" });
});
