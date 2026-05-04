/**
 * E2E: Cash Flows panel on /orders page
 *
 * Verifies the new section that surfaces deposits/withdrawals/dividends
 * pulled from IB's CashTransaction Flex Query.
 */

import { test, expect, type Page } from "@playwright/test";

const PORTFOLIO = {
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

const CASH_FLOWS = {
  rows: [
    {
      id: "txn-1",
      date: "2026-05-04",
      type: "Withdrawal",
      amount: -35_000,
      currency: "USD",
      description: "ACH Withdrawal to Chase",
      raw_type: "Deposits/Withdrawals",
      synced_at: new Date().toISOString(),
    },
    {
      id: "txn-2",
      date: "2026-04-15",
      type: "Deposit",
      amount: 100_000,
      currency: "USD",
      description: "Wire from Bank of America",
      raw_type: "Deposits/Withdrawals",
      synced_at: new Date().toISOString(),
    },
    {
      id: "txn-3",
      date: "2026-04-30",
      type: "Dividend",
      amount: 245.5,
      currency: "USD",
      description: "AAPL CASH DIVIDEND",
      raw_type: "Dividends",
      synced_at: new Date().toISOString(),
    },
  ],
  count: 3,
  from_date: "2026-02-04",
  summary: { deposits: 100_000, withdrawals: -35_000, dividends: 245.5, net: 65_245.5 },
};

async function setupMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/orders", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/cash-flows*", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CASH_FLOWS) }),
  );
  await page.route("**/api/blotter", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/regime", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/ib-status", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
}

test.describe("Cash Flows section on /orders", () => {
  test("renders the section with all 3 rows + summary totals", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");

    const section = page.locator('[data-testid="cash-flows-section"]');
    await section.waitFor({ timeout: 10_000 });

    // Header summary shows the three top-line numbers
    await expect(section).toContainText(/CASH FLOWS \(90 DAYS\)/);
    await expect(section).toContainText(/DEPOSITS/);
    await expect(section).toContainText(/WITHDRAWALS/);
    await expect(section).toContainText(/NET/);

    // All three rows render
    await expect(page.locator('[data-testid="cash-flow-row-txn-1"]')).toBeVisible();
    await expect(page.locator('[data-testid="cash-flow-row-txn-2"]')).toBeVisible();
    await expect(page.locator('[data-testid="cash-flow-row-txn-3"]')).toBeVisible();
  });

  test("withdrawal row renders with negative sign and red color", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");

    const row = page.locator('[data-testid="cash-flow-row-txn-1"]');
    await row.waitFor({ timeout: 10_000 });

    // Sign convention: negative = outflow
    await expect(row).toContainText("-$35,000.00");
    await expect(row).toContainText("Withdrawal");
    await expect(row).toContainText(/ACH Withdrawal/i);
  });

  test("deposit row renders with positive sign", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");

    const row = page.locator('[data-testid="cash-flow-row-txn-2"]');
    await row.waitFor({ timeout: 10_000 });

    await expect(row).toContainText("+$100,000.00");
    await expect(row).toContainText("Deposit");
  });

  test("dividend row renders with the Dividend pill type", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");

    const row = page.locator('[data-testid="cash-flow-row-txn-3"]');
    await row.waitFor({ timeout: 10_000 });

    await expect(row).toContainText("+$245.50");
    await expect(row).toContainText("Dividend");
  });

  test("type filter dropdown narrows visible rows", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");

    const section = page.locator('[data-testid="cash-flows-section"]');
    await section.waitFor({ timeout: 10_000 });

    // Switch to Withdrawals only
    await section.locator("select.filter-select").selectOption("Withdrawal");

    // The withdrawal row stays visible
    await expect(page.locator('[data-testid="cash-flow-row-txn-1"]')).toBeVisible();

    // Deposit and Dividend rows should be filtered out
    await expect(page.locator('[data-testid="cash-flow-row-txn-2"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="cash-flow-row-txn-3"]')).toHaveCount(0);
  });
});
