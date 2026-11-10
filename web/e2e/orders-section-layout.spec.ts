/**
 * E2E: layout + collapsibility on /orders
 *
 * - Cash Flows is rendered AFTER Historical Trades in the DOM
 * - Historical Trades section is collapsible via a clickable header
 *   (same pattern as Cash Flows) and is expanded by default
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
      amount: -1_000,
      currency: "USD",
      description: "ACH Withdrawal",
      raw_type: "Deposits/Withdrawals",
      synced_at: new Date().toISOString(),
    },
  ],
  count: 1,
  from_date: "2026-02-04",
  summary: { deposits: 0, withdrawals: -1_000, dividends: 0, net: -1_000 },
};

const BLOTTER = {
  as_of: new Date().toISOString(),
  summary: { realized_pnl: 0 },
  closed_trades: [
    {
      symbol: "AAPL",
      contract_desc: "AAPL Long Stock",
      sec_type: "STK",
      is_closed: true,
      net_quantity: 100,
      total_quantity: 100,
      realized_quantity: 100,
      total_commission: 1.5,
      realized_pnl: 250,
      cost_basis: 18_000,
      proceeds: 18_250,
      executions: [{ time: "2026-05-04T10:00:00Z" }],
    },
  ],
  open_trades: [],
};

async function setupMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/orders", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/cash-flows*", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CASH_FLOWS) }),
  );
  await page.route("**/api/blotter", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(BLOTTER) }),
  );
  await page.route("**/api/regime", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/ib-status", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
}

test.describe("/orders section layout + collapse", () => {
  test("Historical Trades renders before Cash Flows", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");

    const cashFlows = page.locator('[data-testid="cash-flows-section"]');
    const historical = page.locator('[data-testid="historical-trades-section"]');
    await cashFlows.waitFor({ timeout: 10_000 });
    await historical.waitFor({ timeout: 10_000 });

    const historicalPrecedesCashFlows = await page.evaluate(() => {
      const c = document.querySelector('[data-testid="cash-flows-section"]')!;
      const h = document.querySelector('[data-testid="historical-trades-section"]')!;
      // DOCUMENT_POSITION_PRECEDING = 2 → historical precedes cash flows
      return Boolean(c.compareDocumentPosition(h) & 2);
    });
    expect(historicalPrecedesCashFlows).toBe(true);
  });

  test("Historical Trades has a collapsible header with chevron", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");

    const section = page.locator('[data-testid="historical-trades-section"]');
    await section.waitFor({ timeout: 10_000 });

    const toggle = section.locator('[data-testid="historical-trades-toggle"]');
    await expect(toggle).toHaveAttribute("aria-expanded", "true");

    // Body visible by default
    await expect(section.locator("table")).toBeVisible();

    // Clicking collapses
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(section.locator("table")).toHaveCount(0);

    // Clicking again re-expands
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(section.locator("table")).toBeVisible();
  });

  test("Refresh button + filter input do NOT toggle Historical Trades collapse", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");

    const section = page.locator('[data-testid="historical-trades-section"]');
    await section.waitFor({ timeout: 10_000 });

    const toggle = section.locator('[data-testid="historical-trades-toggle"]');
    await expect(toggle).toHaveAttribute("aria-expanded", "true");

    // Interact with filter input — must not collapse
    const filter = page.getByPlaceholder("Filter historical trades...");
    await filter.fill("AAPL");
    await expect(toggle).toHaveAttribute("aria-expanded", "true");

    // Clear and click refresh — must not collapse
    await filter.fill("");
    await section.locator("button.sync-button").click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
  });
});
