import { expect, test } from "@playwright/test";

const INSUFFICIENT = ["ADBE", "CBRS", "GLD", "META", "SLV", "SOFI", "SPCX", "VIX", "WULF"];

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
  risk_budget: {
    clusters: [],
    breaches: [],
    aggregate_exposure: 0,
    insufficient_data: INSUFFICIENT,
    corr_threshold: 0.7,
    book_budget: 0.025,
  },
};

const ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 72,
      permId: 653611397,
      symbol: "CBRS",
      contract: {
        conId: 742392001,
        symbol: "CBRS",
        secType: "OPT",
        strike: 230,
        right: "P",
        expiry: "2026-08-14",
      },
      action: "BUY",
      orderType: "LMT",
      totalQuantity: 35,
      limitPrice: 3.5,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 35,
      avgFillPrice: null,
      tif: "DAY",
    },
  ],
  executed_orders: [],
  open_count: 1,
  executed_count: 0,
};

async function stubApis(page: import("@playwright/test").Page) {
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
  );
  await page.route("**/api/portfolio**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ score: 15, cri: { score: 15 } }),
    }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        as_of: new Date().toISOString(),
        summary: { realized_pnl: 0 },
        closed_trades: [],
        open_trades: [],
      }),
    }),
  );
}

/**
 * Measure the header's title and gate in a SINGLE evaluate.
 *
 * Two sequential `boundingBox()` calls are two round-trips, and the modal
 * reflows once shortly after it opens (the mono webfont swaps in and every
 * row above the banner re-measures). Straddling that reflow compares a
 * pre-shift `title.y` against a post-shift `gate.y` and reports a gap no
 * single frame ever had - main measured 5.85 against this 6px tolerance
 * purely by luck. Reading both rects in one frame, after fonts settle, is
 * what "on one row" actually means, and it is stricter than the old form:
 * a genuine wrap puts the gap at a full row height (~24px), not 4px.
 */
async function headerGeometry(banner: import("@playwright/test").Locator) {
  await banner.page().evaluate(() => document.fonts?.ready);
  return banner.evaluate((el) => {
    const rect = (selector: string) => {
      const node = el.querySelector(selector);
      if (!node) return null;
      const { x, y, width } = node.getBoundingClientRect();
      return { x, y, width };
    };
    return { title: rect(".crb-title"), gate: rect(".crb-gate") };
  });
}

test("modify modal keeps correlation risk header on one row with ticker chips", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.addInitScript(() => {
    localStorage.setItem("theme", "dark");
  });
  await stubApis(page);
  await page.goto("/orders");

  const row = page.locator("tbody tr").filter({ hasText: "CBRS" }).first();
  await expect(row).toBeVisible({ timeout: 10_000 });
  await row.getByRole("button", { name: "MODIFY" }).click();

  const banner = page.getByTestId("correlation-risk-banner");
  await expect(banner).toBeVisible();
  await expect(banner.locator(".crb-headline")).toContainText(/Gate 3:.*correlation/i);
  await expect(banner.locator(".crb-detail")).toContainText(/price history/i);

  const { title: titleBox, gate: gateBox } = await headerGeometry(banner);
  expect(titleBox).toBeTruthy();
  expect(gateBox).toBeTruthy();
  expect(Math.abs((titleBox?.y ?? 0) - (gateBox?.y ?? 0))).toBeLessThan(6);
  expect((gateBox?.x ?? 0)).toBeGreaterThan((titleBox?.x ?? 0) + (titleBox?.width ?? 0) - 4);

  await expect(banner.locator(".crb-ticker")).toHaveText(INSUFFICIENT);
  const dialog = page.locator(".modify-dialog");
  await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Modify Order" })).toBeVisible();

  await page.setViewportSize({ width: 393, height: 852 });
  const { title: titleMobile, gate: gateMobile } = await headerGeometry(banner);
  expect(titleMobile).toBeTruthy();
  expect(gateMobile).toBeTruthy();
  expect(Math.abs((titleMobile?.y ?? 0) - (gateMobile?.y ?? 0))).toBeLessThan(6);
});
