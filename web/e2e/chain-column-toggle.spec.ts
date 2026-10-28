/**
 * E2E: options chain COLUMNS toggle.
 *
 * Every greek and quote heading can be shown or hidden from the chain
 * toolbar; both sides follow one map, the grid widens to keep a per-column
 * floor, and the choice survives a reload.
 */

import { test, expect, type Page } from "@playwright/test";

const PORTFOLIO = {
  bankroll: 100_000, peak_value: 100_000, last_sync: new Date().toISOString(),
  total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
  position_count: 0, defined_risk_count: 0, undefined_risk_count: 0,
  avg_kelly_optimal: null, exposure: {}, violations: [], positions: [],
};
const ORDERS = {
  last_sync: new Date().toISOString(), open_orders: [], executed_orders: [],
  open_count: 0, executed_count: 0,
};
const EXPIRATIONS = { symbol: "IWO", expirations: ["20991218"] };
const CHAIN = {
  symbol: "IWO", expiry: "20991218", exchange: "SMART", multiplier: "100",
  strikes: Array.from({ length: 30 }, (_, i) => 300 + i * 5),
};

function json(body: unknown) {
  return { status: 200, contentType: "application/json", body: JSON.stringify(body) };
}

async function stubApis(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (r) => r.fulfill(json(PORTFOLIO)));
  await page.route("**/api/orders", (r) => r.fulfill(json(ORDERS)));
  await page.route("**/api/regime", (r) => r.fulfill(json({ score: 10, cri: { score: 10 } })));
  await page.route("**/api/ib-status", (r) => r.fulfill(json({ connected: false })));
  await page.route("**/api/profile", (r) => r.fulfill(json({})));
  await page.route("**/api/blotter", (r) =>
    r.fulfill(json({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] })),
  );
  await page.route("**/api/ticker/**", (r) =>
    r.fulfill(json({ uw_info: { name: "iShares Russell 2000 Growth", sector: "ETF" }, stock_state: {}, profile: {}, stats: {} })),
  );
  await page.route("**/api/options/expirations*", (r) => r.fulfill(json(EXPIRATIONS)));
  await page.route("**/api/options/chain*", (r) => r.fulfill(json(CHAIN)));
  await page.route("**/api/prices", (r) => r.abort());
}

async function headerRow(page: Page): Promise<string[]> {
  return (await page.locator('[data-testid="chain-columns-header"] tr').first().locator("th").allTextContents())
    .map((t) => t.trim());
}

test.describe("Options chain column toggle", () => {
  test("shows, hides and persists greek and quote columns", async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1000 });
    await stubApis(page);
    await page.goto("/IWO?tab=chain");
    await page.locator("tr.chain-row").first().waitFor({ timeout: 30_000 });

    expect(await headerRow(page)).toEqual([
      "Δ", "IV", "Implied", "Vol", "Bid", "Mid", "Ask", "Last", "Strike",
      "Last", "Bid", "Mid", "Ask", "Vol", "Implied", "IV", "Δ",
    ]);

    const trigger = page.getByRole("button", { name: /columns/i });
    await expect(trigger).toContainText("8/15");
    await trigger.click();
    const menu = page.getByRole("menu");
    await page.screenshot({ path: "test-results/chain-column-toggle-open.png" });
    await menu.getByRole("button", { name: "Show all Greeks" }).click();
    await menu.getByRole("checkbox", { name: "Bid IV" }).check();
    await menu.getByRole("checkbox", { name: "Ask IV" }).check();
    await menu.getByRole("checkbox", { name: "Model price" }).check();
    await expect(trigger).toContainText("15/15");
    await page.screenshot({ path: "test-results/chain-column-toggle-menu.png" });

    const headers = await headerRow(page);
    expect(headers).toHaveLength(31);
    for (const h of ["Γ", "Θ", "Vega", "ρ", "Bid IV", "Ask IV", "Model"]) {
      expect(headers.filter((x) => x === h)).toHaveLength(2);
    }
    // Every body row carries one cell per header.
    await expect(page.locator("tr.chain-row").first().locator("td")).toHaveCount(31);

    // Columns keep a usable width: the grid scrolls instead of crushing.
    const gridWidth = await page.locator(".chain-anchor-header .chain-grid").evaluate((el) => el.getBoundingClientRect().width);
    expect(gridWidth).toBeGreaterThanOrEqual(30 * 60);

    await menu.getByRole("checkbox", { name: "Last" }).uncheck();
    await trigger.click();
    await expect(menu).toHaveCount(0);
    expect(await headerRow(page)).not.toContain("Last");

    await page.reload();
    await page.locator("tr.chain-row").first().waitFor({ timeout: 30_000 });
    await expect.poll(async () => (await headerRow(page)).length).toBe(29);
    expect(await headerRow(page)).not.toContain("Last");
    await page.screenshot({ path: "test-results/chain-column-toggle-all-greeks.png" });
  });
});
