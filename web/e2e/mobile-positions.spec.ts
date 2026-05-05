import { test, expect, type Page } from "@playwright/test";

const TODAY = "2026-03-24";

const PORTFOLIO_MOCK = {
  bankroll: 1_000_000,
  peak_value: 1_000_000,
  last_sync: `${TODAY}T14:34:25Z`,
  total_deployed_pct: 0.16,
  total_deployed_dollars: -1571.92,
  remaining_capacity_pct: 99.84,
  position_count: 2,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 1_000_000,
    daily_pnl: 200,
    unrealized_pnl: 200,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 400_000,
    dividends: 0,
  },
  positions: [
    {
      id: 1,
      ticker: "AAPL",
      structure: "Long Call ($200)",
      structure_type: "Long Call",
      risk_profile: "defined",
      expiry: "2026-04-18",
      contracts: 5,
      direction: "LONG",
      entry_cost: 1500,
      max_risk: 1500,
      market_value: 1750,
      market_price_is_calculated: false,
      ib_daily_pnl: 50,
      entry_date: "2026-03-20",
      kelly_optimal: null,
      target: null,
      stop: null,
      legs: [
        {
          direction: "LONG",
          contracts: 5,
          type: "Call",
          strike: 200,
          entry_cost: 1500,
          avg_cost: 300,
          market_price: 3.5,
          market_value: 1750,
        },
      ],
    },
    {
      id: 2,
      ticker: "MSFT",
      structure: "Stock",
      structure_type: "Stock",
      risk_profile: "equity",
      expiry: "N/A",
      contracts: 100,
      direction: "LONG",
      entry_cost: 40000,
      max_risk: null,
      market_value: 41000,
      market_price_is_calculated: false,
      ib_daily_pnl: 100,
      entry_date: "2026-02-01",
      kelly_optimal: null,
      target: null,
      stop: null,
      legs: [
        {
          direction: "LONG",
          contracts: 100,
          type: "Stock",
          strike: null,
          entry_cost: 40000,
          avg_cost: 400,
          market_price: 410,
          market_value: 41000,
        },
      ],
    },
  ],
};

const ORDERS_EMPTY = {
  open_orders: [],
  executed_orders: [],
  last_sync: `${TODAY}T14:34:25Z`,
};

async function setupMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_MOCK) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: `${TODAY}T14:34:25Z`, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  await page.route("**/api/cash-flows**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [], summary: {} }) }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

test.describe("Mobile positions list", () => {
  test("renders a card per position with metrics + expand-to-show-legs", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    // Multiple section instances (defined/undefined/equity), so at least one mobile-position-list mounts
    const lists = page.getByTestId("mobile-position-list");
    await expect(lists.first()).toBeVisible();

    const aapl = page.getByTestId("mobile-position-AAPL");
    const msft = page.getByTestId("mobile-position-MSFT");
    await expect(aapl).toBeVisible();
    await expect(msft).toBeVisible();

    await expect(aapl).toContainText("AAPL");
    await expect(aapl).toContainText("Long Call");
    await expect(aapl).toContainText("MV");
    await expect(aapl).toContainText("EC");

    // Legs should not be visible initially
    await expect(page.getByTestId("mobile-position-AAPL-legs")).toHaveCount(0);

    // Tap the card to expand
    await aapl.click({ force: true });
    await expect(page.getByTestId("mobile-position-AAPL-legs")).toBeVisible();
    await expect(page.getByTestId("mobile-position-AAPL-legs")).toContainText("LONG");
    await expect(page.getByTestId("mobile-position-AAPL-legs")).toContainText("Call");
  });

  test("desktop table is hidden on mobile (no <table>)", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    await expect(page.getByTestId("mobile-position-list").first()).toBeVisible();
    const tables = page.locator(".table-wrap table");
    await expect(tables).toHaveCount(0);
  });

  test("position cards are tappable (>=44px)", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    const aapl = page.getByTestId("mobile-position-AAPL");
    const box = await aapl.boundingBox();
    expect(box).not.toBeNull();
    if (box) {
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
  });

  test("body[data-mobile=true] is set on /portfolio", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");
    await expect(page.getByTestId("mobile-position-list").first()).toBeVisible();
    const dataMobile = await page.evaluate(() => document.body.dataset.mobile);
    expect(dataMobile).toBe("true");
  });
});
