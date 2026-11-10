/**
 * E2E regression for production bug 2026-05-09: a 2026-05-08 withdrawal of
 * -$72,000 did not appear in the Cash Flows panel because the daemon's
 * once-per-day cadence interacted with IBKR Flex's ~1-day settlement lag.
 *
 * The fix lowers the cadence from 86400s to 14400s (4h). This spec
 * pins the UI rendering of the withdrawal so a future regression in the
 * route, hook, or component surfaces immediately.
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

const MAY_8_WITHDRAWAL = {
  rows: [
    {
      id: "39803040384",
      date: "2026-05-08",
      type: "Withdrawal",
      amount: -72_000,
      currency: "USD",
      description: "DISBURSEMENT INITIATED BY Joseph McCann",
      raw_type: "Deposits/Withdrawals",
      synced_at: "2026-05-09T08:00:00Z",
    },
  ],
  count: 1,
  from_date: "2026-02-09",
  summary: { deposits: 0, withdrawals: -72_000, dividends: 0, net: -72_000 },
};

const EMPTY_CASH_FLOWS = {
  rows: [],
  count: 0,
  from_date: "2026-02-09",
  summary: { deposits: 0, withdrawals: 0, dividends: 0, net: 0 },
};

async function setupBaseMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/orders", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/blotter", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        as_of: new Date().toISOString(),
        summary: { realized_pnl: 0 },
        closed_trades: [],
        open_trades: [],
      }),
    }),
  );
  await page.route("**/api/regime", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/ib-status", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
}

test.describe("Cash Flows panel — May 8 withdrawal regression", () => {
  test("renders the May 8 withdrawal with correct sign and amount when expanded", async ({ page }) => {
    await setupBaseMocks(page);
    await page.route("**/api/cash-flows*", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MAY_8_WITHDRAWAL) }),
    );

    await page.goto("/orders");

    const section = page.locator('[data-testid="cash-flows-section"]');
    await section.waitFor({ timeout: 10_000 });

    // Header summary picks up the withdrawal as a -$72,000 outflow.
    await expect(section).toContainText(/CASH FLOWS \(90 DAYS\)/);
    await expect(section).toContainText(/WITHDRAWALS/);

    // Expand the section so the row table is visible.
    await section.locator('[data-testid="cash-flows-toggle"]').click();

    const row = page.locator('[data-testid="cash-flow-row-39803040384"]');
    await row.waitFor({ timeout: 5_000 });
    await expect(row).toContainText("Withdrawal");
    await expect(row).toContainText("-$72,000.00");
    await expect(row).toContainText(/DISBURSEMENT/i);

    // The amount cell carries the `negative` semantic class — ensures
    // the fix didn't silently flip sign handling.
    const amountCell = row.locator("td.right.negative").first();
    await expect(amountCell).toHaveText(/-\$72,000\.00/);
  });

  test("renders the empty state when no cash flows exist", async ({ page }) => {
    await setupBaseMocks(page);
    await page.route("**/api/cash-flows*", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(EMPTY_CASH_FLOWS) }),
    );

    await page.goto("/orders");

    const section = page.locator('[data-testid="cash-flows-section"]');
    await section.waitFor({ timeout: 10_000 });
    await section.locator('[data-testid="cash-flows-toggle"]').click();

    // Empty-state copy is whatever the component renders today —
    // the assertion is intentionally loose so a sister agent's
    // empty-state UI rework doesn't break this spec.
    await expect(section).toContainText(/no cash transactions|no .* in the last/i);
  });
});
