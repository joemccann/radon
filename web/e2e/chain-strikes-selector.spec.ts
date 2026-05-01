/**
 * E2E: Options chain strikes-per-side selector
 *
 * Verifies that:
 *  1. The dropdown contains ±10, ±15, ±25, ±50, ±100, and All options.
 *  2. Selecting ±100 renders more strike rows than ±15 (the default).
 *  3. Selecting All renders all strikes returned by the chain API.
 *  4. The $650 put row is visible when ±100 is selected and the chain
 *     has $1 increments (SPY-like), where $650 is 70+ strikes from ATM.
 */

import { test, expect } from "@playwright/test";

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
const EXPIRATIONS = { symbol: "SPY", expirations: ["20260821", "20260918"] };

// 160 strikes at $1 increments around a $720 ATM, matching real SPY chain shape
const ALL_STRIKES = Array.from({ length: 160 }, (_, i) => 640 + i);  // 640..799
const CHAIN_DATA = {
  symbol: "SPY",
  expiry: "20260918",
  exchange: "SMART",
  strikes: ALL_STRIKES,
  multiplier: "100",
};

function stubApis(page: import("@playwright/test").Page, overrideChain = CHAIN_DATA) {
  page.route("**/api/portfolio", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  page.route("**/api/orders", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
  );
  page.route("**/api/regime", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 10, cri: { score: 10 } }) }),
  );
  page.route("**/api/ib-status", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
  );
  page.route("**/api/blotter", (r) =>
    r.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  page.route("**/api/ticker/**", (r) =>
    r.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        uw_info: { name: "SPDR S&P 500 ETF", sector: "ETF", description: "SPY" },
        stock_state: {}, profile: {}, stats: {},
      }),
    }),
  );
  page.route("**/api/options/expirations*", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(EXPIRATIONS) }),
  );
  page.route("**/api/options/chain*", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(overrideChain) }),
  );
  page.route("**/api/prices", (r) => r.abort());
}

async function openChain(page: import("@playwright/test").Page) {
  await page.goto("/SPY?tab=chain");
  await page.locator(".chain-grid").waitFor({ timeout: 12_000 });
}

async function countVisibleStrikeRows(page: import("@playwright/test").Page) {
  return page.locator("tr.chain-row").count();
}

async function setStrikesPerSide(page: import("@playwright/test").Page, value: string) {
  const select = page.locator("select.chain-expiry-select").last();
  await select.selectOption(value);
  // Brief wait for React re-render
  await page.waitForTimeout(100);
}

test.describe("Chain strikes-per-side selector", () => {
  test("dropdown contains all expected options including ±100 and All", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await openChain(page);

    const select = page.locator("select.chain-expiry-select").last();
    const options = await select.locator("option").allTextContents();

    expect(options).toContain("±10");
    expect(options).toContain("±15");
    expect(options).toContain("±25");
    expect(options).toContain("±50");
    expect(options).toContain("±100");
    expect(options).toContain("All");
  });

  test("±100 renders more rows than the default ±15", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await openChain(page);

    const defaultRows = await countVisibleStrikeRows(page);

    await setStrikesPerSide(page, "100");
    const wideRows = await countVisibleStrikeRows(page);

    expect(wideRows).toBeGreaterThan(defaultRows);
    // ±100 should show at most 201 rows (100 below + ATM + 100 above)
    expect(wideRows).toBeLessThanOrEqual(201);
  });

  test("All renders every strike from the chain API", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await openChain(page);

    await setStrikesPerSide(page, "-1");
    const rows = await countVisibleStrikeRows(page);

    // Should render all 160 strikes defined in CHAIN_DATA
    expect(rows).toBe(ALL_STRIKES.length);
  });

  test("$650 strike row is visible after selecting ±100 with $1-increment chain", async ({ page }) => {
    // ATM is ~$720 (middle of 640-799). $650 is 70 strikes below ATM.
    // ±50 only reaches $670 — $650 is outside that window.
    // ±100 covers down to $620 — $650 must appear.
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await openChain(page);

    // Verify $650 is NOT visible at default ±15
    const rows15 = page.locator("tr.chain-row td.chain-strike").filter({ hasText: "650" });
    await expect(rows15).toHaveCount(0);

    // Switch to ±100 — $650 must now appear
    await setStrikesPerSide(page, "100");
    const rows100 = page.locator("tr.chain-row td.chain-strike").filter({ hasText: /^\$?650(\.00)?$/ });
    await expect(rows100).toHaveCount(1);
  });

  test("All mode: $650 strike row is visible", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await openChain(page);

    await setStrikesPerSide(page, "-1");
    const row = page.locator("tr.chain-row td.chain-strike").filter({ hasText: /^\$?650(\.00)?$/ });
    await expect(row).toHaveCount(1);
  });

  test("switching from All back to ±15 reduces row count", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await openChain(page);

    // Go wide then narrow — should restore limited view
    await setStrikesPerSide(page, "-1");
    const allRows = await countVisibleStrikeRows(page);

    await setStrikesPerSide(page, "15");
    const narrowRows = await countVisibleStrikeRows(page);

    expect(narrowRows).toBeLessThan(allRows);
    expect(narrowRows).toBeLessThanOrEqual(31); // ±15 = max 31 rows
  });
});
