/**
 * E2E: brand-aligned empty states on /orders.
 *
 * Stubs orders.json + blotter.json + portfolio.json to be empty and
 * confirms both the Open Orders and Today's Executed Orders panels
 * render the SectionEmptyState (icon + headline + secondary copy)
 * instead of the legacy bare alert-item text.
 */

import { test, expect, type Page } from "@playwright/test";

const PORTFOLIO_EMPTY = {
  bankroll: 1_500_000,
  peak_value: 1_500_000,
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

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const BLOTTER_EMPTY = {
  as_of: new Date().toISOString(),
  summary: { realized_pnl: 0 },
  closed_trades: [],
  open_trades: [],
};

const CASH_FLOWS_EMPTY = {
  rows: [],
  count: 0,
  from_date: "2026-02-04",
  summary: { deposits: 0, withdrawals: 0, dividends: 0, net: 0 },
};

async function setupEmptyMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
  );
  await page.route("**/api/orders", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/blotter", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(BLOTTER_EMPTY) }),
  );
  await page.route("**/api/cash-flows*", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CASH_FLOWS_EMPTY) }),
  );
  await page.route("**/api/regime", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/ib-status", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
}

test.describe("/orders empty-state surfaces", () => {
  test("Open Orders empty state renders icon + headline + secondary copy", async ({ page }) => {
    await setupEmptyMocks(page);
    await page.goto("/orders");

    const empty = page.locator('[data-testid="open-orders-empty"]');
    await empty.waitFor({ timeout: 10_000 });

    await expect(empty).toBeVisible();
    await expect(empty.locator('[data-testid="section-empty-state-icon"]')).toBeVisible();
    await expect(empty).toContainText("No working orders");
    await expect(empty).toContainText("Place an order from any ticker view");

    // Legacy bare text + chevron must be gone.
    await expect(page.locator(".alert-item", { hasText: /^No open orders$/ })).toHaveCount(0);
  });

  test("Today's Executed Orders empty state renders icon + headline + secondary copy", async ({ page }) => {
    await setupEmptyMocks(page);
    await page.goto("/orders");

    const empty = page.locator('[data-testid="today-executed-empty"]');
    await empty.waitFor({ timeout: 10_000 });

    await expect(empty).toBeVisible();
    await expect(empty.locator('[data-testid="section-empty-state-icon"]')).toBeVisible();
    await expect(empty).toContainText("No fills today");
    await expect(empty).toContainText(/Executions during today's session/);

    await expect(page.locator(".alert-item", { hasText: /^No fills this session$/ })).toHaveCount(0);
  });

  test("Historical Trades empty state renders icon + headline + refresh action", async ({ page }) => {
    await setupEmptyMocks(page);
    await page.goto("/orders");

    await page.locator('[data-testid="historical-trades-toggle"]').click();

    const empty = page.locator('[data-testid="historical-trades-empty"]');
    await empty.waitFor({ timeout: 10_000 });

    await expect(empty).toBeVisible();
    await expect(empty.locator('[data-testid="section-empty-state-icon"]')).toBeVisible();
    await expect(empty).toContainText("No historical trades");
    await expect(empty).toContainText(/Pull the last 30 days/);
    await expect(empty.locator('[data-testid="section-empty-state-action"]')).toBeVisible();

    await expect(page.locator(".alert-item", { hasText: /No historical trades/ })).toHaveCount(0);
  });

  test("Cash Flows empty state renders icon + headline + secondary copy", async ({ page }) => {
    await setupEmptyMocks(page);
    await page.goto("/orders");

    await page.locator('[data-testid="cash-flows-toggle"]').click();

    const empty = page.locator('[data-testid="cash-flows-empty"]');
    await empty.waitFor({ timeout: 10_000 });

    await expect(empty).toBeVisible();
    await expect(empty.locator('[data-testid="section-empty-state-icon"]')).toBeVisible();
    await expect(empty).toContainText("No cash transactions in the last 90 days");
    await expect(empty).toContainText(/Deposits, withdrawals, dividends, and fees/);

    await expect(page.locator(".alert-item", { hasText: /No cash transactions/ })).toHaveCount(0);
  });
});
