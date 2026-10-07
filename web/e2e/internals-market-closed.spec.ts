import { expect, test } from "@playwright/test";

const INTERNALS_MOCK_CLOSED = {
  scan_time: "2026-03-22T09:00:00-04:00",
  market_open: false,
  date: "2026-03-22",
  vix: 24.23,
  vvix: 120.11,
  spy: 575.32,
  vix_5d_roc: 3.2,
  vvix_vix_ratio: 4.96,
  spx_100d_ma: 570.12,
  spx_distance_pct: 0.91,
  cor1m: 31.45,
  cor1m_previous_close: 30.95,
  cor1m_5d_change: 1.2,
  realized_vol: 12.4,
  cri: { score: 22, level: "ELEVATED", components: { vix: 7, vvix: 6, correlation: 5, momentum: 4 } },
  cta: { realized_vol: 12.4, exposure_pct: 88, forced_reduction_pct: 12, est_selling_bn: 18 },
  menthorq_cta: null,
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false },
    values: {},
  },
  history: [],
  spy_closes: [],
  nasdaq_skew: 0.125,
  nq_skew: 0.125,
  spx_skew: -0.225,
  nq_skew_history: [
    { date: "2026-03-20", nq_skew: 0.125, spx_position: 0.5, nq_position: 0.625 },
  ],
  spx_skew_history: [
    { date: "2026-03-20", spx_skew: -0.225 },
  ],
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

async function freezeToClosedWeekend(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    const fixedNow = new Date("2026-03-22T12:00:00Z").valueOf();
    const RealDate = Date;
    class MockDate extends RealDate {
      constructor(...args: ConstructorParameters<typeof Date>) {
        if (args.length === 0) {
          super(fixedNow);
          return;
        }
        super(...args);
      }
      static now() {
        return fixedNow;
      }
    }
    Object.defineProperty(window, "Date", {
      value: MockDate,
      configurable: true,
      writable: true,
    });
  });
}

async function setupMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await freezeToClosedWeekend(page);

  await page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(INTERNALS_MOCK_CLOSED),
    }),
  );
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
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, days_until_expiry: 14 }),
    }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: false }),
    }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
}

test.describe("/internals closed-market load", () => {
  test("redirects the retired internals route to the populated CRI workspace", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/internals");

    await expect(page).toHaveURL(/\/regime\/cri$/);
    await expect(page.locator('[data-testid="strip-vix"]:visible').first()).toContainText("24.23");
    await expect(page.locator(".regime-hero, .m-regime-headline").filter({ visible: true }).first()).toBeVisible();
    await expect(page.getByTestId("internals-nq-skew-chart")).toHaveCount(0);
    await expect(page.locator(".regime-empty", { hasText: "Loading internals..." })).toHaveCount(0);
  });
});
