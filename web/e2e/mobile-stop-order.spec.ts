import { expect, test, type Locator, type Page, type Request } from "@playwright/test";

test.use({ viewport: { width: 393, height: 852 }, serviceWorkers: "block" });

async function stubChainApis(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        positions: [],
        last_sync: new Date().toISOString(),
        bankroll: 0,
        peak_value: 0,
        total_deployed_pct: 0,
        total_deployed_dollars: 0,
        remaining_capacity_pct: 100,
        position_count: 0,
        defined_risk_count: 0,
        undefined_risk_count: 0,
        avg_kelly_optimal: null,
        exposure: {},
        violations: [],
      }),
    }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        open_orders: [],
        executed_orders: [],
        open_count: 0,
        executed_count: 0,
        last_sync: new Date().toISOString(),
      }),
    }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/ticker/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ uw_info: { name: "Apple Inc.", sector: "Tech", description: "" }, stock_state: {}, profile: {}, stats: {} }),
    }),
  );
  await page.route("**/api/options/expirations*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ symbol: "AAPL", expirations: ["20260320", "20260417"] }),
    }),
  );
  await page.route("**/api/options/chain*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        symbol: "AAPL",
        expiry: "20260320",
        exchange: "SMART",
        strikes: [195, 200, 205, 210, 215],
        multiplier: "100",
      }),
    }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

async function tapJs(locator: Locator) {
  await locator.evaluate((el) => (el as HTMLElement).click());
}

test("mobile single-leg ticket posts STP", async ({ page }) => {
  await stubChainApis(page);
  let placeBody: Record<string, unknown> | null = null;
  await page.route("**/api/orders/place", async (route, request: Request) => {
    placeBody = await request.postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, orderId: 9999 }),
    });
  });

  await page.goto("/AAPL?tab=chain");
  await tapJs(page.getByTestId("mobile-chain-call-200"));
  await tapJs(page.getByTestId("mobile-chain-detail-buy"));
  await tapJs(page.getByTestId("mobile-chain-pending-strip"));
  await expect(page.getByTestId("mobile-order-ticket")).toBeVisible();

  await tapJs(page.getByTestId("order-type-stp"));
  await page.getByTestId("order-stop-price").fill("2.50");
  await tapJs(page.getByTestId("mobile-order-ticket-review"));
  await tapJs(page.getByTestId("mobile-order-ticket-submit"));

  await expect(page.getByTestId("mobile-order-ticket-success")).toBeVisible();
  expect(placeBody).not.toBeNull();
  expect(placeBody!.orderType).toBe("STP");
  expect(placeBody!.stopPrice).toBe(2.5);
  expect(placeBody!.type).toBe("option");
});
