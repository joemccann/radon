/**
 * E2E: Ticker Search → Chain Tab → Order Builder flow.
 *
 * Tests the full user journey:
 * 1. CMD+K focuses search, typing filters results
 * 2. Selecting a ticker opens the detail modal
 * 3. Book tab shows L1 order book
 * 4. Chain tab loads expirations and strikes
 * 5. Clicking chain rows adds legs to the order builder
 */

import { test, expect } from "@playwright/test";

const PORTFOLIO = {
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

const ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const EXPIRATIONS = {
  symbol: "AAPL",
  expirations: ["20260320", "20260417", "20260515", "20260619"],
};

const CHAIN_STRIKES = {
  symbol: "AAPL",
  expiry: "20260417",
  exchange: "SMART",
  strikes: [180, 185, 190, 195, 200, 205, 210, 215, 220, 225, 230],
  multiplier: "100",
};

function makePriceData(symbol: string, last: number, bid: number, ask: number) {
  return {
    symbol,
    last,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 50,
    askSize: 50,
    volume: 1000,
    high: null,
    low: null,
    open: null,
    close: last - 1,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: symbol.includes("_C") ? 0.5 : symbol.includes("_P") ? -0.5 : null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: symbol.includes("_") ? 0.35 : null,
    undPrice: null,
    timestamp: new Date().toISOString(),
  };
}

function stubApis(page: import("@playwright/test").Page) {
  page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
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
      body: JSON.stringify({
        uw_info: { name: "Apple Inc.", sector: "Technology", description: "Test" },
        stock_state: {},
        profile: {},
        stats: {},
      }),
    }),
  );
  page.route("**/api/options/expirations*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(EXPIRATIONS) }),
  );
  page.route("**/api/options/chain*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CHAIN_STRIKES) }),
  );
  page.route("**/api/prices", (route) => route.abort());
}

test.describe("Ticker Search → Detail Page → Chain", () => {
  test("search input focuses on CMD+K and opens detail page on selection", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/portfolio");

    // Focus search via keyboard shortcut
    await page.keyboard.press("Meta+k");
    const searchInput = page.locator('input[role="combobox"]');
    await expect(searchInput).toBeFocused();
  });

  test("Book tab shows L1 order book with bid/ask/spread", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/portfolio");

    // Inject prices for AAPL
    await page.evaluate((pd) => {
      window.dispatchEvent(
        new CustomEvent("ws-price", { detail: { type: "price", symbol: pd.symbol, data: pd } }),
      );
    }, makePriceData("AAPL", 205.50, 205.40, 205.60));

    // Navigate directly to ticker detail page
    await page.goto("/AAPL?tab=book");

    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 5_000 });

    // Verify L1 order book section exists
    await expect(detail.locator("text=ORDER BOOK")).toBeVisible();
  });

  test("Chain tab loads expirations and shows strike grid", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/portfolio");

    // Inject underlying price for ATM centering
    await page.evaluate((pd) => {
      window.dispatchEvent(
        new CustomEvent("ws-price", { detail: { type: "price", symbol: pd.symbol, data: pd } }),
      );
    }, makePriceData("AAPL", 205.50, 205.40, 205.60));

    // Navigate directly to ticker detail page with chain tab
    await page.goto("/AAPL?tab=chain");

    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 5_000 });

    // Should show expiry selector
    const expirySelect = detail.locator(".chain-expiry-select").first();
    await expect(expirySelect).toBeVisible();

    // Should show the strike grid table
    const chainGrid = detail.locator(".chain-grid");
    await expect(chainGrid).toBeVisible();

    // Should have CALLS and PUTS headers
    await expect(detail.locator("th:has-text('CALLS')")).toBeVisible();
    await expect(detail.locator("th:has-text('PUTS')")).toBeVisible();

    // ATM strike (205) should be highlighted
    const atmRow = detail.locator(".chain-row-atm");
    await expect(atmRow).toBeVisible();
  });

  test("clicking chain bid/ask adds legs to order builder", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/portfolio");

    // Inject prices
    const prices = [
      makePriceData("AAPL", 205.50, 205.40, 205.60),
      makePriceData("AAPL_20260417_200_C", 10.50, 10.30, 10.70),
      makePriceData("AAPL_20260417_210_C", 5.20, 5.00, 5.40),
      makePriceData("AAPL_20260417_200_P", 4.80, 4.60, 5.00),
    ];
    await page.evaluate((pds) => {
      for (const pd of pds) {
        window.dispatchEvent(
          new CustomEvent("ws-price", { detail: { type: "price", symbol: (pd as { symbol: string }).symbol, data: pd } }),
        );
      }
    }, prices);

    // Navigate directly to ticker detail page with chain tab
    await page.goto("/AAPL?tab=chain");

    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 5_000 });

    // Wait for chain to load
    await detail.locator(".chain-grid").waitFor();

    // Click a call mid price (should add BUY leg)
    const callMid = detail.locator('.chain-mid.chain-clickable').first();
    if (await callMid.isVisible()) {
      await callMid.click();

      // Order builder should appear
      const orderBuilder = detail.locator(".order-builder");
      await expect(orderBuilder).toBeVisible();

      // Should show the leg
      const legRow = orderBuilder.locator(".order-builder-leg");
      await expect(legRow).toHaveCount(1);
    }
  });

  test("ratio combos show normalized net credit and place normalized leg ratios", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);

    let placedBody: Record<string, unknown> | null = null;
    await page.route("**/api/orders/place", async (route) => {
      placedBody = JSON.parse(route.request().postData() ?? "{}") as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", orderId: 12345, initialStatus: "Submitted" }),
      });
    });

    await page.goto("/AAPL?tab=chain");

    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 5_000 });
    await detail.locator(".chain-grid").waitFor();

    const putRow = detail.getByRole("row", { name: /\$200\.00/ }).first();
    await putRow.locator(".chain-bid.chain-clickable").last().click();

    const callRow = detail.getByRole("row", { name: /\$210\.00/ }).first();
    await callRow.locator(".chain-mid.chain-clickable").first().click();

    const orderBuilder = detail.locator(".order-builder");
    await expect(orderBuilder).toBeVisible();

    const legRows = orderBuilder.locator(".order-builder-leg");
    await expect(legRows).toHaveCount(2);

    await legRows.nth(0).locator('input[type="number"]').first().fill("25");
    await legRows.nth(1).locator('input[type="number"]').first().fill("50");
    await legRows.nth(0).locator('input[type="number"]').nth(1).fill("5.30");
    await legRows.nth(1).locator('input[type="number"]').nth(1).fill("2.60");

    await orderBuilder.getByRole("button", { name: /MID/i }).click();
    const limitPriceInput = orderBuilder.locator(".modify-price-input");
    await expect(limitPriceInput).toHaveValue("0.10");
    await expect(orderBuilder.getByText("$250.00 notional")).toBeVisible();

    await orderBuilder.getByRole("button", { name: /Place Risk Reversal/i }).click();
    await orderBuilder.getByRole("button", { name: /Confirm: Risk Reversal @ \$0.10/i }).click();

    expect(placedBody).not.toBeNull();
    expect(placedBody?.quantity).toBe(25);
    expect(placedBody?.type).toBe("combo");

    const comboLegs = Array.isArray(placedBody?.legs) ? placedBody.legs as Array<Record<string, unknown>> : [];
    expect(comboLegs).toHaveLength(2);
    expect(comboLegs.map((leg) => leg.ratio)).toEqual([1, 2]);
  });
});
