/**
 * E2E: /orders command strip, partial fill display, combo cancel confirm.
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

function makeOpenOrder(overrides: Record<string, unknown> = {}) {
  const totalQuantity = (overrides.totalQuantity as number | undefined) ?? 10;
  return {
    orderId: 1,
    permId: 1001,
    symbol: "AAPL",
    contract: {
      conId: 1,
      symbol: "AAPL",
      secType: "STK",
      strike: null,
      right: null,
      expiry: null,
    },
    action: "BUY",
    orderType: "LMT",
    totalQuantity,
    limitPrice: 100,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: totalQuantity,
    avgFillPrice: null,
    tif: "DAY",
    ...overrides,
  };
}

async function stubOrdersPage(page: Page, orders: unknown) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/orders**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(orders) }),
  );
  await page.route("**/api/blotter", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), closed_trades: [], open_trades: [], summary: {} }),
    }),
  );
  await page.route("**/api/cash-flows*", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ rows: [], count: 0, summary: {} }),
    }),
  );
  await page.route("**/api/regime", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15 }) }),
  );
  await page.route("**/api/ib-status", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
}

test.describe("/orders UX improvements", () => {
  test("command strip shows counts and jump anchors", async ({ page }) => {
    await stubOrdersPage(page, {
      last_sync: new Date().toISOString(),
      open_count: 2,
      executed_count: 0,
      executed_orders: [],
      open_orders: [
        makeOpenOrder({ filled: 0, remaining: 10, totalQuantity: 10, permId: 1 }),
        makeOpenOrder({ filled: 4, remaining: 6, totalQuantity: 10, permId: 2, orderId: 2 }),
      ],
    });

    await page.goto("/orders");
    const strip = page.getByTestId("orders-command-strip");
    await expect(strip).toBeVisible();
    await expect(strip).toContainText("Working");
    await expect(strip).toContainText("Partial");
    await expect(strip.locator('a[href="#orders-open"]')).toBeVisible();
    await expect(strip.locator('a[href="#orders-historical"]')).toBeVisible();
  });

  test("partial fill shows 4/10 and Partial status", async ({ page }) => {
    await stubOrdersPage(page, {
      last_sync: new Date().toISOString(),
      open_count: 1,
      executed_count: 0,
      executed_orders: [],
      open_orders: [
        makeOpenOrder({ filled: 4, remaining: 6, totalQuantity: 10, status: "Submitted" }),
      ],
    });

    await page.goto("/orders");
    await expect(page.getByText("4/10")).toBeVisible();
    await expect(page.getByText("Partial").first()).toBeVisible();
    await expect(page.getByText("Δ Fill")).toBeVisible();
  });

  test("combo CANCEL ALL opens confirm dialog and does not cancel until confirmed", async ({ page }) => {
    let cancelPosts = 0;
    await stubOrdersPage(page, {
      last_sync: new Date().toISOString(),
      open_count: 2,
      executed_count: 0,
      executed_orders: [],
      open_orders: [
        makeOpenOrder({
          orderId: 10,
          permId: 10,
          symbol: "AMD",
          action: "BUY",
          limitPrice: 2,
          totalQuantity: 1,
          contract: {
            conId: 10,
            symbol: "AMD",
            secType: "OPT",
            strike: 100,
            right: "C",
            expiry: "2026-08-21",
          },
        }),
        makeOpenOrder({
          orderId: 11,
          permId: 11,
          symbol: "AMD",
          action: "SELL",
          limitPrice: 1,
          totalQuantity: 1,
          contract: {
            conId: 11,
            symbol: "AMD",
            secType: "OPT",
            strike: 110,
            right: "C",
            expiry: "2026-08-21",
          },
        }),
      ],
    });

    await page.route("**/api/orders/cancel", async (route) => {
      cancelPosts += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, orders: { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 } }),
      });
    });

    await page.goto("/orders");
    const cancelAll = page.getByRole("button", { name: "CANCEL ALL" });
    await expect(cancelAll).toBeVisible();
    await cancelAll.click();

    // Modal title is a labelled span (role=dialog name), not a heading.
    const dialog = page.getByRole("dialog", { name: "Cancel Orders" });
    await expect(dialog).toBeVisible();
    expect(cancelPosts).toBe(0);

    await dialog.getByRole("button", { name: "Cancel All" }).click();
    await expect.poll(() => cancelPosts).toBeGreaterThan(0);
  });
});
