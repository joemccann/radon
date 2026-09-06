import { test, expect, type Page } from "@playwright/test";
import type { CashFlowResponse } from "../lib/useCashFlows";

const TODAY = "2026-03-24";
const CASH_FLOWS_EMPTY = {
  rows: [], count: 0, from_date: "2025-12-24",
  summary: { deposits: 0, withdrawals: 0, dividends: 0, net: 0 },
  last_synced_at: null,
} satisfies CashFlowResponse;
const CASH_FLOWS_POPULATED = {
  ...CASH_FLOWS_EMPTY,
  rows: [
    { id: "deposit", date: "2026-03-23", type: "Deposit", amount: 1_000, currency: "USD", description: "Test transfer in", raw_type: null, synced_at: `${TODAY}T15:00:00Z` },
    { id: "withdrawal", date: "2026-03-24", type: "Withdrawal", amount: -125, currency: "USD", description: "Test transfer out", raw_type: null, synced_at: `${TODAY}T15:00:00Z` },
  ],
  count: 2,
  summary: { deposits: 1_000, withdrawals: -125, dividends: 0, net: 875 },
  last_synced_at: `${TODAY}T15:00:00Z`,
} satisfies CashFlowResponse;

const PORTFOLIO_EMPTY = {
  bankroll: 1_000_000,
  peak_value: 1_000_000,
  last_sync: `${TODAY}T14:34:25Z`,
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 1_000_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 400_000,
    dividends: 0,
  },
  positions: [],
};

const ORDERS_EMPTY = { open_count: 0, executed_count: 0, open_orders: [], executed_orders: [], last_sync: `${TODAY}T14:34:25Z` };

const BLOTTER_MOCK = {
  as_of: `${TODAY}T14:34:25Z`,
  summary: { closed_trades: 1, open_trades: 0, total_commissions: 1.0, realized_pnl: 250 },
  closed_trades: [
    {
      symbol: "AAPL",
      contract_desc: "AAPL 200C 2026-04-18",
      sec_type: "OPT",
      is_closed: true,
      net_quantity: 0,
      total_quantity: 5,
      total_commission: 1.0,
      realized_pnl: 250,
      realized_quantity: 5,
      realized_cost_basis: 1500,
      cost_basis: 1500,
      proceeds: 1750,
      total_cash_flow: 250,
      executions: [{ time: "2026-03-23T15:00:00Z", price: 3.5, quantity: 5, side: "SELL" }],
    },
  ],
  open_trades: [],
};

async function setupMocks(page: Page) {
  await page.clock.setFixedTime(new Date(`${TODAY}T16:00:00Z`));
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/**", (route) => route.fulfill({ status: 503, json: { error: "Unmocked test endpoint" } }));
  await page.route("**/api/portfolio**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(BLOTTER_MOCK) }),
  );
  await page.route("**/api/cash-flows**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CASH_FLOWS_EMPTY) }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

test.describe("Mobile Historical Trades blotter", () => {
  test("renders trade cards instead of the desktop table", async ({ page }, testInfo) => {
    await setupMocks(page);
    await page.goto("/orders");

    await expect(page.getByTestId("mobile-blotter-list")).toBeVisible();
    const card = page.getByTestId("mobile-blotter-AAPL-0");
    await expect(card).toBeVisible();
    await expect(card).toContainText("AAPL");
    await expect(card).toContainText("Closed");
    await expect(card).toContainText("AAPL 200C 2026-04-18");
    await expect(card.getByText("Qty", { exact: true })).toBeVisible();
    await expect(card.getByText("5", { exact: true })).toBeVisible();
    await expect(card.getByText("Net P&L", { exact: true })).toBeVisible();
    await expect(card.getByText("+$250.00", { exact: true })).toBeVisible();
    await expect(card.getByText("+16.7%", { exact: true })).toBeVisible();
    await expect(card.getByText("Comm $1.00", { exact: true })).toBeVisible();
    await expect(page.getByText(/NaN/)).toHaveCount(0);
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("mobile-blotter.png") });
  });

  test("desktop blotter table is hidden on mobile", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/orders");
    await expect(page.getByTestId("mobile-blotter-list")).toBeVisible();
    // The historical trades section's <table> should not render on mobile
    const section = page.getByTestId("historical-trades-section");
    await expect(section).toBeVisible();
    const tables = section.locator("table");
    await expect(tables).toHaveCount(0);
  });
});

for (const width of [360, 393]) {
  test(`populated ledger controls fit and respond to touch at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 852 });
    await setupMocks(page);
    await page.route("**/api/cash-flows**", (route) => route.fulfill({ json: CASH_FLOWS_POPULATED }));
    await page.goto("/orders");
    const historical = page.getByTestId("historical-trades-section");
    const cash = page.getByTestId("cash-flows-section");
    await expect(page.getByTestId("mobile-blotter-AAPL-0")).toBeVisible();
    await expect(cash.getByText("2 TXNS", { exact: true })).toBeVisible();

    for (const header of [historical.locator(".section-header"), cash.locator(".section-header")]) {
      const geometry = await header.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        return { right: rect.right, overflow: element.scrollWidth - element.clientWidth };
      });
      expect(geometry.right).toBeLessThanOrEqual(width);
      expect(geometry.overflow).toBeLessThanOrEqual(1);
    }
    const search = historical.getByPlaceholder("Filter historical trades...");
    const refresh = historical.getByRole("button", { name: "Refresh", exact: true });
    const pageSize = historical.getByRole("combobox", { name: "Historical trades page size" });
    const cashFilter = cash.getByRole("combobox");
    for (const [name, control] of [["historical search", search], ["historical refresh", refresh], ["historical page size", pageSize], ["cash filter", cashFilter]] as const) {
      const box = await control.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width);
      expect(box!.height, `${name} touch height`).toBeGreaterThanOrEqual(44);
      expect(box!.width).toBeGreaterThanOrEqual(44);
    }

    await search.click();
    await search.fill("MSFT");
    await expect(page.getByTestId("mobile-blotter-AAPL-0")).toHaveCount(0);
    await historical.getByRole("button", { name: "Clear filter", exact: true }).click();
    await expect(page.getByTestId("mobile-blotter-AAPL-0")).toContainText("+$250.00");
    const refreshed = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/blotter");
    await refresh.click();
    await refreshed;
    await expect(historical.getByText("1 TRADES", { exact: true })).toBeVisible();
    await expect(page.getByTestId("mobile-blotter-AAPL-0")).toContainText("+16.7%");
    const pageSizes = await pageSize.locator("option").evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value));
    for (const value of pageSizes) {
      await pageSize.click();
      await pageSize.press("Escape");
      await pageSize.selectOption(value);
      await pageSize.press("Tab");
      await expect(pageSize).toHaveValue(value);
      await expect(page.getByTestId("mobile-blotter-AAPL-0")).toContainText("+$250.00");
      await expect(page.getByTestId("historical-showing-range")).toHaveText("Showing 1-1 of 1");
    }
    await historical.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`historical-toolbar-${width}.png`) });

    await cash.getByText("CASH FLOWS (90 DAYS)", { exact: true }).click();
    await expect(page.getByTestId("cash-flow-row-deposit")).toContainText("+$1,000.00");
    await expect(page.getByTestId("cash-flow-row-withdrawal")).toContainText("-$125.00");
    const cashTypes = await cashFilter.locator("option").evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value));
    for (const value of cashTypes) {
      await cashFilter.click();
      // Native option pickers are not DOM buttons. Keep the real opening tap,
      // then use Playwright's select interaction for the platform-owned picker.
      await cashFilter.press("Escape");
      await cashFilter.selectOption(value);
      await cashFilter.press("Tab");
      await expect(cashFilter).toHaveValue(value);
      const depositVisible = value === "all" || value === "Deposit";
      const withdrawalVisible = value === "all" || value === "Withdrawal";
      await expect(page.getByTestId("cash-flow-row-deposit")).toHaveCount(depositVisible ? 1 : 0);
      await expect(page.getByTestId("cash-flow-row-withdrawal")).toHaveCount(withdrawalVisible ? 1 : 0);
      if (depositVisible) await expect(page.getByTestId("cash-flow-row-deposit")).toContainText("+$1,000.00");
      if (withdrawalVisible) await expect(page.getByTestId("cash-flow-row-withdrawal")).toContainText("-$125.00");
      await expect(cash.getByText(`${Number(depositVisible) + Number(withdrawalVisible)} TXNS`, { exact: true })).toBeVisible();
      await expect(cash.locator(".cash-flows-stat").filter({ hasText: "NET" })).toContainText("+$875.00");
      const textFit = await cashFilter.evaluate((element: HTMLSelectElement) => {
        const style = getComputedStyle(element);
        const context = document.createElement("canvas").getContext("2d")!;
        context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        const selected = element.selectedOptions[0].text;
        const label = style.textTransform === "uppercase" ? selected.toUpperCase() : selected;
        return { measured: context.measureText(label).width, available: element.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 24 };
      });
      expect(textFit.measured, `${value} option text fits without clipping`).toBeLessThanOrEqual(textFit.available);
    }
    await cashFilter.selectOption("WithholdingTax");
    await cash.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`cash-long-filter-${width}.png`) });
    await cashFilter.selectOption("Withdrawal");
    await expect(page.getByTestId("cash-flow-row-withdrawal")).toContainText("-$125.00");
    await cash.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`cash-toolbar-${width}.png`) });
  });
}
