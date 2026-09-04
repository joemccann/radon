import { expect, test, type Page } from "@playwright/test";

const LAST_SYNC = "2026-09-04T14:30:00Z";

const PORTFOLIO_MOCK = {
  bankroll: 400_000,
  peak_value: 400_000,
  last_sync: LAST_SYNC,
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 400_000,
    daily_pnl: -120,
    unrealized_pnl: -10_803,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 400_000,
    dividends: 0,
  },
  positions: [{
    id: 1,
    ticker: "ARM",
    structure: "Combo (3 legs)",
    structure_type: "Combo (3 legs)",
    risk_profile: "defined",
    expiry: "2026-09-18",
    contracts: 10,
    direction: "LONG",
    entry_cost: null,
    max_risk: null,
    market_value: -1_393,
    basis_source: "mixed",
    ib_daily_pnl: -120,
    kelly_optimal: null,
    target: null,
    stop: null,
    legs: [
      { direction: "SHORT", contracts: 10, type: "Call", strike: 260, entry_cost: 8_000, avg_cost: 800, market_price: 8.6, market_value: 8_600, basis_source: "session_fills" },
      { direction: "LONG", contracts: 10, type: "Call", strike: 270, entry_cost: 10_380, avg_cost: 1_038, market_price: 5.525, market_value: 5_525, basis_source: "ib" },
      { direction: "LONG", contracts: 10, type: "Put", strike: 220, entry_cost: 7_030, avg_cost: 703, market_price: 1.682, market_value: 1_682, basis_source: "ib" },
    ],
  }],
};

async function setupMocks(page: Page) {
  await page.route("**/api/portfolio", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(PORTFOLIO_MOCK),
  }));
  await page.route("**/api/orders", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ last_sync: LAST_SYNC, open_orders: [], executed_orders: [] }),
  }));
  await page.route("**/api/flex-token", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ ok: true, days_until_expiry: 14 }),
  }));
  await page.route("**/api/ib-status", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ connected: true }),
  }));
  await page.route("**/api/blotter", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ as_of: LAST_SYNC, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
  }));
  await page.route("**/api/profile", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ username: "Operator", avatar_url: null, ui_preferences: null }),
  }));
  await page.route("**/api/preferences", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({}),
  }));
  await page.route("**/api/prices**", (route) => route.abort());
}

test("ARM protected combo is defined risk and shows aggregate P&L", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1800, height: 900 });
  await page.addInitScript(() => window.localStorage.setItem("theme", "dark"));
  await setupMocks(page);
  await page.goto("/portfolio", { waitUntil: "domcontentloaded" });

  const definedSection = page.locator(".section").filter({ hasText: "Defined Risk Positions" });
  const armRow = definedSection.locator("tr").filter({ hasText: "ARM" });
  await expect(definedSection).toBeVisible();
  await expect(armRow).toHaveCount(1);
  await expect(armRow).toContainText("-$10,803");
  await expect(armRow).toContainText("N/A");
  await expect(page.getByText("Undefined Risk Positions")).toHaveCount(0);

  await armRow.getByRole("button", { name: "Expand legs for ARM" }).click();
  await expect(definedSection.locator("tr").filter({ hasText: "SHORT Call $260" })).toContainText("-$600");
  await expect(definedSection.locator("tr").filter({ hasText: "LONG Call $270" })).toContainText("-$4,855");
  await expect(definedSection.locator("tr").filter({ hasText: "LONG Put $220" })).toContainText("-$5,348");

  const screenshotPath = testInfo.outputPath("arm-defined-combo-pnl.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await testInfo.attach("arm-defined-combo-pnl", { path: screenshotPath, contentType: "image/png" });
});
