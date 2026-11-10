import { expect, test } from "@playwright/test";

const STALE_CLOSE_TRANSITION = {
  scan_time: "2026-03-12T13:02:06.090911",
  market_open: false,
  date: "2026-03-11",
  vix: 24.23,
  vvix: 122.49,
  spy: 676.33,
  vix_5d_roc: 14.6,
  vvix_vix_ratio: 5.06,
  realized_vol: 11.51,
  cor1m: 29.18,
  cor1m_previous_close: 28.87,
  cor1m_5d_change: 11.23,
  spx_100d_ma: 682.39,
  spx_distance_pct: -0.87,
  spy_closes: Array.from({ length: 40 }, (_, index) => 680 - index * 0.4),
  cri: { score: 27, level: "ELEVATED", components: { vix: 8, vvix: 11, correlation: 5, momentum: 3 } },
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: true, realized_vol_gt_25: false, cor1m_gt_60: false },
  },
  cta: { exposure_pct: 86.9, forced_reduction_pct: 13.1, est_selling_bn: 52.4 },
  menthorq_cta: null,
  history: [],
};

const TODAY_CLOSE = {
  ...STALE_CLOSE_TRANSITION,
  scan_time: "2026-03-12T13:03:13.251409",
  date: "2026-03-12",
  vix: 26.72,
  vvix: 130.18,
  spy: 666.06,
  realized_vol: 12.55,
  cri: { score: 31, level: "ELEVATED", components: { vix: 9.5, vvix: 12.4, correlation: 5.4, momentum: 3.7 } },
};

const PORTFOLIO_EMPTY = {
  bankroll: 100_000,
  positions: [],
  account_summary: {},
  exposure: {},
  violations: [],
};

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

async function setupMocks(page: import("@playwright/test").Page) {
  let getCount = 0;
  let postCount = 0;
  let servedStaleGet = false;

  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/regime", (route) => {
    const method = route.request().method();
    if (method === "POST") {
      postCount += 1;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(STALE_CLOSE_TRANSITION),
      });
    }

    getCount += 1;
    if (getCount === 1) {
      servedStaleGet = true;
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(getCount >= 2 ? TODAY_CLOSE : STALE_CLOSE_TRANSITION),
    });
  });

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_EMPTY),
    }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_EMPTY),
    }),
  );
  await page.route("**/api/prices", (route) => route.abort());
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: false }),
    }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/menthorq/cta", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tables: [] }),
    }),
  );

  return {
    counts: () => ({ getCount, postCount, servedStaleGet }),
  };
}

test.describe("/regime page — close transition freshness", () => {
  test("auto-refreshes from a stale prior-session payload to today's closing values without manual sync", async ({ page }) => {
    const tracker = await setupMocks(page);
    await page.goto("/regime");

    const vixCell = page.locator('[data-testid="strip-vix"]');
    await expect(page.locator('[data-testid="market-closed-indicator"]')).toBeVisible();

    await expect(vixCell.locator(".regime-strip-value")).toHaveText("26.72", { timeout: 12_000 });
    await expect(page.locator('[data-testid="strip-vvix"] .regime-strip-value')).toHaveText("130.18");
    await expect(page.locator('[data-testid="strip-spy"] .regime-strip-value')).toHaveText("$666.06");
    await expect(page.locator('[data-testid="strip-rvol"] .regime-strip-value')).toHaveText("12.55%");

    expect(tracker.counts().servedStaleGet).toBe(true);
    await expect.poll(() => tracker.counts().getCount).toBeGreaterThanOrEqual(2);
  });
});
