/**
 * E2E: Mobile Options Chain ladder at 393×852.
 *
 * Validates:
 * 1. Two-column ladder renders for AAPL with calls/strike/puts.
 * 2. Expiry chip bar lists at least the available expirations.
 * 3. Tapping a strike cell opens the detail bottom sheet.
 * 4. The desktop chain table is hidden on mobile.
 */

import { test, expect, type Page } from "@playwright/test";

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

const ORDERS = { last_sync: new Date().toISOString(), open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 };

function stubApis(page: Page) {
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
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  page.route("**/api/cash-flows**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [], summary: {} }) }),
  );
  page.route("**/api/ticker/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ uw_info: { name: "Apple Inc.", sector: "Tech", description: "" }, stock_state: {}, profile: {}, stats: {} }) }),
  );
  page.route("**/api/options/expirations*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ symbol: "AAPL", expirations: ["20260320", "20260417"] }) }),
  );
  page.route("**/api/options/chain*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ symbol: "AAPL", expiry: "20260320", exchange: "SMART", strikes: [195, 200, 205, 210, 215], multiplier: "100" }) }),
  );
  page.route("**/api/prices**", (route) => route.abort());
}

test.describe("Mobile Options Chain ladder", () => {
  test("renders two-column ladder with strike column and CALLS/PUTS labels", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/AAPL?tab=chain");

    await expect(page.getByTestId("mobile-chain")).toBeVisible();
    await expect(page.getByTestId("mobile-chain-ladder")).toBeVisible();

    const head = page.locator(".mobile-chain__ladder-head");
    await expect(head).toContainText("CALLS");
    await expect(head).toContainText("STRIKE");
    await expect(head).toContainText("PUTS");

    // All five strikes from the mock should render
    for (const strike of [195, 200, 205, 210, 215]) {
      await expect(page.getByTestId(`mobile-chain-row-${strike}`)).toBeVisible();
    }
  });

  test("expiry chip bar lists each expiration", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/AAPL?tab=chain");

    await expect(page.getByTestId("mobile-chain-expiry-bar")).toBeVisible();
    await expect(page.getByTestId("mobile-chain-expiry-20260320")).toBeVisible();
    await expect(page.getByTestId("mobile-chain-expiry-20260417")).toBeVisible();
  });

  test("tapping a call cell opens the detail bottom sheet", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/AAPL?tab=chain");

    await page.getByTestId("mobile-chain-call-200").click({ force: true });
    await expect(page.getByTestId("mobile-chain-detail-sheet")).toBeVisible();
    const sheet = page.getByTestId("mobile-chain-detail-sheet");
    await expect(sheet).toContainText("AAPL 200 Call");

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("mobile-chain-detail-sheet")).toBeHidden();
  });

  // iPhone regression (2026-07-21): the chain deck (.asset-deck) is a stacking
  // context at z-index 40, so an inline (non-portaled) sheet was capped below
  // the tab bar (z-index 60) — the tab bar PAINTED OVER the sheet's BUY/SELL
  // footer. Pin the actual paint order: hit-testing the footer's own pixels
  // must reach the sheet, not the tab bar.
  test("detail sheet footer paints above the mobile tab bar", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/AAPL?tab=chain");

    await page.getByTestId("mobile-chain-call-200").click({ force: true });
    const sheet = page.getByTestId("mobile-chain-detail-sheet");
    await expect(sheet).toBeVisible();

    // Let the 240ms slide-in animation settle so the footer rect is final.
    await page.waitForFunction(() => {
      const footer = document.querySelector(".m-sheet__footer");
      if (!footer) return false;
      const r = footer.getBoundingClientRect();
      return r.bottom <= window.innerHeight && r.height > 0;
    });

    const buriedByTabBar = await page.evaluate(() => {
      const footer = document.querySelector(".m-sheet__footer")!;
      const r = footer.getBoundingClientRect();
      const probe = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (!probe) return "no-element";
      if (probe.closest(".mobile-sheet-root")) return "sheet-on-top";
      return `covered-by:${probe.className}`;
    });
    expect(buriedByTabBar).toBe("sheet-on-top");
  });

  test("desktop chain table is not rendered on mobile", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/AAPL?tab=chain");

    await expect(page.getByTestId("mobile-chain")).toBeVisible();
    await expect(page.locator(".chain-grid")).toHaveCount(0);
  });

  test("side toggle and strikes selector reach desktop parity", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/AAPL?tab=chain");

    await expect(page.getByTestId("mobile-chain-side-toggle")).toBeVisible();
    await expect(page.getByTestId("mobile-chain-strikes-select")).toBeVisible();

    // Both sides render by default.
    await expect(page.getByTestId("mobile-chain-call-200")).toBeVisible();
    await expect(page.getByTestId("mobile-chain-put-200")).toBeVisible();

    // CALLS hides the put column.
    await page.getByTestId("mobile-chain-side-calls").click();
    await expect(page.getByTestId("mobile-chain-call-200")).toBeVisible();
    await expect(page.getByTestId("mobile-chain-put-200")).toHaveCount(0);
    await expect(page.locator(".mobile-chain__ladder-head")).not.toContainText("PUTS");

    // PUTS hides the call column.
    await page.getByTestId("mobile-chain-side-puts").click();
    await expect(page.getByTestId("mobile-chain-put-200")).toBeVisible();
    await expect(page.getByTestId("mobile-chain-call-200")).toHaveCount(0);

    // Back to ALL restores both.
    await page.getByTestId("mobile-chain-side-both").click();
    await expect(page.getByTestId("mobile-chain-call-200")).toBeVisible();
    await expect(page.getByTestId("mobile-chain-put-200")).toBeVisible();
  });

  test("changing expiry chip swaps the active expiration", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);
    await page.goto("/AAPL?tab=chain");

    const first = page.getByTestId("mobile-chain-expiry-20260320");
    const second = page.getByTestId("mobile-chain-expiry-20260417");
    await expect(first).toBeVisible();

    await second.click();
    await expect(second).toHaveAttribute("aria-pressed", "true");
    await expect(first).toHaveAttribute("aria-pressed", "false");
  });
});
