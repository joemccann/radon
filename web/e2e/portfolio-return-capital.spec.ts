import { expect, test, type Page } from "@playwright/test";

const PORTFOLIO_MOCK = {
  bankroll: 1_300_000,
  peak_value: 1_300_000,
  last_sync: "2026-08-07T18:30:00Z",
  total_deployed_pct: 4.2,
  total_deployed_dollars: 54_600,
  remaining_capacity_pct: 95.8,
  position_count: 3,
  defined_risk_count: 1,
  undefined_risk_count: 2,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  trade_log_dates: {},
  account_summary: {
    net_liquidation: 1_300_000,
    daily_pnl: 0,
    unrealized_pnl: 29_364,
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
      account_id: "U1",
      position_instance_id: "PI-SPCX-1",
      ticker: "SPCX",
      structure: "Ratio Risk Reversal 60x30 (P$120.0/C$135.0)",
      structure_type: "Ratio Risk Reversal",
      risk_profile: "undefined",
      expiry: "2026-08-21",
      contracts: 60,
      direction: "COMBO",
      entry_cost: -1_514,
      max_risk: null,
      return_capital: {
        version: 2,
        amount: 40_000,
        currency: "USD",
        measurement: {
          quality: "observed",
          method: "isolated-account-margin-delta",
          measured_at: "2026-07-01T15:31:00Z",
          observation_id: "OBS-SPCX-1",
          isolation: "isolated",
          before_sample_id: "S1",
          after_sample_id: "S2",
          window_seconds: 60,
          concurrent_exec_ids: [],
        },
        linkage: {
          state: "linked",
          account_id: "U1",
          position_instance_id: "PI-SPCX-1",
          con_ids: [101, 102],
          order_refs: ["radon-spcx-1"],
          perm_ids: [9001],
          exec_ids: ["E1", "E2"],
          legs: [
            { con_id: 101, currency: "USD", multiplier: 100 },
            { con_id: 102, currency: "USD", multiplier: 100 },
          ],
        },
      },
      market_value: 26_850,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-07-01",
      legs: [
        { con_id: 101, direction: "LONG", contracts: 60, type: "Call", strike: 135, entry_cost: 48_429, avg_cost: 807.15, market_price: 7, market_value: 42_000 },
        { con_id: 102, direction: "SHORT", contracts: 30, type: "Put", strike: 120, entry_cost: 49_943, avg_cost: 1_664.77, market_price: 5.05, market_value: 15_150 },
      ],
    },
    {
      id: 2,
      ticker: "GLD",
      structure: "Long Call $450",
      structure_type: "Long Call",
      risk_profile: "defined",
      expiry: "2027-01-15",
      contracts: 10,
      direction: "LONG",
      entry_cost: 5_000,
      max_risk: 10_000,
      market_value: 6_000,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-08-07",
      legs: [
        { direction: "LONG", contracts: 10, type: "Call", strike: 450, entry_cost: 5_000, avg_cost: 500, market_price: 6, market_value: 6_000 },
      ],
    },
    {
      id: 3,
      account_id: "U1",
      position_instance_id: "PI-META-1",
      ticker: "META",
      structure: "Ratio Risk Reversal",
      structure_type: "Ratio Risk Reversal",
      risk_profile: "undefined",
      expiry: "2026-08-21",
      contracts: 10,
      direction: "COMBO",
      entry_cost: -1_000,
      max_risk: null,
      return_capital: {
        version: 2,
        amount: 20_000,
        currency: "USD",
        measurement: {
          quality: "estimated",
          method: "ib-whatif",
          measured_at: "2026-08-07T18:29:00Z",
        },
        linkage: {
          state: "linked",
          account_id: "U1",
          position_instance_id: "PI-META-1",
          con_ids: [201, 202],
          order_refs: ["radon-meta-1"],
          perm_ids: [9002],
          exec_ids: ["E3", "E4"],
          legs: [
            { con_id: 201, currency: "USD", multiplier: 100 },
            { con_id: 202, currency: "USD", multiplier: 100 },
          ],
        },
      },
      market_value: 5_000,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-08-07",
      legs: [
        { con_id: 201, direction: "LONG", contracts: 10, type: "Call", strike: 650, entry_cost: 6_000, avg_cost: 600, market_price: 8, market_value: 8_000 },
        { con_id: 202, direction: "SHORT", contracts: 10, type: "Put", strike: 550, entry_cost: 7_000, avg_cost: 700, market_price: 3, market_value: 3_000 },
      ],
    },
  ],
};

async function setupMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(PORTFOLIO_MOCK),
  }));
  await page.route("**/api/orders", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ open_orders: [], executed_orders: [], last_sync: PORTFOLIO_MOCK.last_sync }),
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
    body: JSON.stringify({ as_of: PORTFOLIO_MOCK.last_sync, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
  }));
  await page.route("**/api/prices**", (route) => route.abort());
}

test("portfolio renders isolated observed Return % and suppresses premium return", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await setupMocks(page);
  await page.goto("/portfolio", { waitUntil: "domcontentloaded" });

  const returnHeader = page.getByRole("columnheader", { name: /Return %/ }).first();
  await expect(returnHeader).toBeVisible();
  const returnLabel = returnHeader.locator(".sort-label");
  const returnLabelText = returnHeader.locator(".sort-label > span:first-child");
  const returnHelp = returnHeader.locator("[data-sort-ignore='true']");
  await expect(returnLabel).toHaveCSS("white-space", "nowrap");
  const [labelBox, textBox, helpBox] = await Promise.all([
    returnLabel.boundingBox(),
    returnLabelText.boundingBox(),
    returnHelp.boundingBox(),
  ]);
  expect(labelBox).not.toBeNull();
  expect(textBox).not.toBeNull();
  expect(helpBox).not.toBeNull();
  expect(textBox!.height).toBeLessThanOrEqual(labelBox!.height);
  expect(Math.abs((textBox!.y + textBox!.height / 2) - (helpBox!.y + helpBox!.height / 2))).toBeLessThanOrEqual(2);

  const spcxRow = page.locator("tr").filter({ hasText: "SPCX" });
  await expect(spcxRow).toHaveCount(1);
  await expect(spcxRow.locator('[title*="isolated broker-observed"]')).toHaveText("+70.9%");
  await expect(spcxRow).not.toContainText("1873");

  const gldRow = page.locator("tr").filter({ hasText: "GLD" });
  await expect(gldRow).toHaveCount(1);
  await expect(gldRow.locator('[title*="Return on max risk"]')).toHaveText("+10.0%");

  const metaRow = page.locator("tr").filter({ hasText: "META" });
  await expect(metaRow).toHaveCount(1);
  await expect(metaRow.locator('[title*="Return unavailable"]')).toHaveText("N/A");
  await expect(metaRow).not.toContainText("600.0%");

  const screenshotPath = testInfo.outputPath("portfolio-return.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await testInfo.attach("portfolio-return", {
    path: screenshotPath,
    contentType: "image/png",
  });
});
