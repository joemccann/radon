import { expect, test } from "@playwright/test";

const PORTFOLIO = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 1,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [
    {
      id: 1,
      ticker: "AAOI",
      structure: "Risk Reversal",
      structure_type: "Risk Reversal",
      risk_profile: "Undefined",
      expiry: "2026-03-27",
      contracts: 50,
      direction: "COMBO",
      entry_cost: 0,
      max_risk: null,
      market_value: 0,
      legs: [
        { direction: "SHORT", contracts: 50, type: "Put", strike: 90, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
        { direction: "LONG", contracts: 50, type: "Call", strike: 98, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
      ],
      ib_daily_pnl: null,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-18",
    },
  ],
  account_summary: {
    net_liquidation: 100_000,
    daily_pnl: null,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 200_000,
    dividends: 0,
  },
};

const ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 77,
      permId: 653611587,
      symbol: "AAOI Spread",
      contract: {
        conId: 28812380,
        symbol: "AAOI",
        secType: "BAG",
        strike: 0,
        right: "?",
        expiry: null,
        comboLegs: [
          { conId: 859556931, ratio: 1, action: "SELL", symbol: "AAOI", strike: 90, right: "P", expiry: "2026-03-27" },
          { conId: 861002104, ratio: 1, action: "BUY", symbol: "AAOI", strike: 98, right: "C", expiry: "2026-03-27" },
        ],
      },
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 50,
      limitPrice: 0.6,
      auxPrice: 0,
      status: "Submitted",
      filled: 0,
      remaining: 50,
      avgFillPrice: 0,
      tif: "DAY",
    },
  ],
  executed_orders: [],
  open_count: 1,
  executed_count: 0,
};

async function stubApis(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        as_of: new Date().toISOString(),
        summary: { closed_trades: 0, open_trades: 0, total_commissions: 0, realized_pnl: 0 },
        closed_trades: [],
        open_trades: [],
      }),
    }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/prices", (route) => route.abort());
}

test.describe("Combo order modify flow", () => {
  test("submits combo replacement payload with edited quantity and legs", async ({ page }) => {
    await stubApis(page);

    let modifyBody: Record<string, unknown> | null = null;
    await page.route("**/api/orders/modify", async (route) => {
      modifyBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", message: "Replacement placed", orders: ORDERS }),
      });
    });

    await page.goto("/orders");

    const row = page.locator("tbody tr").filter({ hasText: "AAOI" }).first();
    await expect(row).toBeVisible({ timeout: 10_000 });
    await row.getByRole("button", { name: "MODIFY" }).click();

    const modal = page.locator(".modify-dialog");
    await expect(modal).toBeVisible();
    await expect(modal.getByText("Edit Legs")).toBeVisible();

    const modalContent = page.locator(".modal-content");
    const box = await modalContent.boundingBox();
    expect(box?.width ?? 0).toBeGreaterThan(720);

    const overflow = await modal.locator(".modify-secondary-panel").evaluate((el) => ({
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
    }));
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1);

    await modal.locator("#modify-price-input").focus();
    const focusedPriceLayout = await modal.locator("#modify-price-input").evaluate((input) => {
      const row = input.closest(".modify-price-input-row");
      const field = input.closest(".modify-field");
      const primaryPanel = input.closest(".modify-primary-panel");
      if (!row || !field || !primaryPanel) {
        throw new Error("Missing modify order price field layout");
      }
      const rowBox = row.getBoundingClientRect();
      const fieldBox = field.getBoundingClientRect();
      const panel = primaryPanel as HTMLElement;
      return {
        rowLeft: rowBox.left,
        rowRight: rowBox.right,
        fieldLeft: fieldBox.left,
        fieldRight: fieldBox.right,
        inputBoxShadow: getComputedStyle(input).boxShadow,
        rowFocusWithin: row.matches(":focus-within"),
        panelScrollWidth: panel.scrollWidth,
        panelClientWidth: panel.clientWidth,
      };
    });
    expect(focusedPriceLayout.rowLeft).toBeGreaterThanOrEqual(focusedPriceLayout.fieldLeft - 1);
    expect(focusedPriceLayout.rowRight).toBeLessThanOrEqual(focusedPriceLayout.fieldRight + 1);
    expect(focusedPriceLayout.panelScrollWidth).toBeLessThanOrEqual(focusedPriceLayout.panelClientWidth + 1);
    expect(focusedPriceLayout.inputBoxShadow).toBe("none");
    expect(focusedPriceLayout.rowFocusWithin).toBe(true);

    await expect(modal.locator("#modify-quantity-input")).toHaveValue("50");
    await expect(modal.locator("#modify-leg-0-strike")).toHaveValue("90");
    await expect(modal.locator("#modify-leg-1-strike")).toHaveValue("98");
    await expect(modal.locator("#modify-leg-0-action")).toBeVisible();
    await expect(modal.locator("#modify-leg-0-expiry")).toBeVisible();
    await expect(modal.locator("#modify-leg-1-ratio")).toBeVisible();

    await modal.locator("#modify-quantity-input").fill("75");
    await modal.locator("#modify-price-input").fill("0.75");
    await modal.locator("#modify-leg-1-strike").fill("100");
    await modal.getByRole("button", { name: /modify order/i }).click();

    await expect.poll(() => modifyBody).not.toBeNull();
    expect(modifyBody).toMatchObject({
      orderId: 77,
      permId: 653611587,
      replaceOrder: {
        type: "combo",
        symbol: "AAOI",
        action: "SELL",
        quantity: 75,
        limitPrice: 0.75,
        tif: "DAY",
        legs: [
          { action: "SELL", right: "P", strike: 90, expiry: "20260327", ratio: 1 },
          { action: "BUY", right: "C", strike: 100, expiry: "20260327", ratio: 1 },
        ],
      },
    });
  });
});
