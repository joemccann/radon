import { expect, test, type Page, type Request } from "@playwright/test";

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

test("desktop ticket posts STP with stopPrice", async ({ page }) => {
  stubApis(page);
  let placeBody: Record<string, unknown> | null = null;
  await page.route("**/api/orders/place", async (route, request: Request) => {
    placeBody = await request.postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", orderId: 1, permId: 2 }),
    });
  });

  await page.goto("/AAPL");
  const ticket = page.locator(".act-ticket");
  await ticket.waitFor({ timeout: 30_000 });
  await ticket.getByTestId("order-type-stp").click();
  await ticket.getByTestId("order-stop-price").fill("170");
  await ticket.locator("input.order-input").first().fill("10");
  await ticket.getByRole("button", { name: "Place Order" }).click();
  await ticket.getByRole("button", { name: "Confirm Order" }).click();

  await expect.poll(() => placeBody).not.toBeNull();
  expect(placeBody!.orderType).toBe("STP");
  expect(placeBody!.stopPrice).toBe(170);
  expect(placeBody!.action).toBe("BUY");
  expect(placeBody!.quantity).toBe(10);
});
