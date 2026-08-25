import { expect, test } from "@playwright/test";

// Fixed historical timestamps intentionally force stale-recovery behavior;
// the fresh-snapshot case overrides last_sync at test runtime.
const PORTFOLIO = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-08-13T20:00:00Z",
  positions: [{
    id: 1,
    ticker: "AAPL",
    structure: "Stock",
    structure_type: "Stock",
    risk_profile: "defined",
    expiry: "",
    contracts: 10,
    direction: "LONG",
    entry_cost: 2_000,
    max_risk: 2_000,
    market_value: 2_100,
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-08-13",
    legs: [],
  }],
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 100_000,
    daily_pnl: 100,
    unrealized_pnl: 100,
    realized_pnl: 0,
    settled_cash: 97_900,
    maintenance_margin: 1_000,
    excess_liquidity: 99_000,
    buying_power: 396_000,
    dividends: 0,
  },
};

const ORDERS = {
  last_sync: "2026-08-13T20:00:00Z",
  open_count: 1,
  executed_count: 0,
  executed_orders: [],
  open_orders: [{
    orderId: 1,
    permId: 1001,
    symbol: "AAPL",
    contract: {
      conId: 1,
      symbol: "AAPL",
      secType: "STK",
      strike: null,
      right: null,
      expiry: null,
    },
    action: "BUY",
    orderType: "LMT",
    totalQuantity: 10,
    limitPrice: 100,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: 10,
    avgFillPrice: null,
    tif: "DAY",
  }],
};

test("portfolio shell owns one initial read and shares its symbols with the command palette", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  let portfolioReads = 0;

  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    const payloads: Record<string, unknown> = {
      "/api/orders": { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 },
      "/api/watchlist": { watchlist: [] },
      "/api/service-health": { services: [] },
      "/api/profile": { username: "Operator", avatar_url: null },
      "/api/alerts": { alerts: [] },
      "/api/flex-token": { remaining: 240 },
      "/api/previous-close": { closes: {} },
    };
    if (path === "/api/portfolio") portfolioReads += 1;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(path === "/api/portfolio" ? PORTFOLIO : (payloads[path] ?? {})),
    });
  });

  await page.goto("/portfolio", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("AAPL", { exact: true }).first()).toBeVisible();

  // A fixed 1.2s window could not tell "one owner" from "slow runner": a shell
  // regression issuing a third read at t=1.3s passed, and a dev remount storm
  // inside the window failed. Settle on an explicit signal, then WATCH the
  // counter - a late owner has to show up in the watch window, and a slow first
  // paint cannot be mistaken for one.
  await page.waitForLoadState("networkidle");
  const settled = portfolioReads;
  expect(settled, "the portfolio shell must issue its own read").toBeGreaterThan(0);
  // next dev intentionally exercises React's development remount path. It may
  // issue the shell's mount read twice; opening the always-mounted palette must
  // not create a third request owner.
  expect(settled, "one read owner, plus at most one dev remount").toBeLessThanOrEqual(2);

  const watchDeadline = Date.now() + 2_500;
  while (Date.now() < watchDeadline) {
    expect(
      portfolioReads,
      "a new /api/portfolio owner appeared after the page settled",
    ).toBe(settled);
    await page.waitForTimeout(100);
  }
  const readsBeforePalette = portfolioReads;

  await page.keyboard.press("Meta+k");
  const palette = page.getByRole("dialog", { name: "Command palette" });
  await expect(palette).toBeVisible();
  await palette.getByRole("textbox", { name: "Search" }).fill("AAPL");
  await expect(palette.getByText("AAPL", { exact: true })).toBeVisible();
  expect(portfolioReads).toBe(readsBeforePalette);

  const screenshotPath = testInfo.outputPath("portfolio-request-cadence.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await testInfo.attach("portfolio-request-cadence", {
    path: screenshotPath,
    contentType: "image/png",
  });
});

test("initial cached portfolio read paints before stale recovery can POST", async ({ page }) => {
  test.setTimeout(90_000);
  let releasePortfolioGet!: () => void;
  const portfolioGetGate = new Promise<void>((resolve) => {
    releasePortfolioGet = resolve;
  });
  let releasePortfolioPost!: () => void;
  const portfolioPostGate = new Promise<void>((resolve) => {
    releasePortfolioPost = resolve;
  });
  let portfolioGets = 0;
  let portfolioPosts = 0;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const payloads: Record<string, unknown> = {
      "/api/orders": { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 },
      "/api/watchlist": { watchlist: [] },
      "/api/service-health": { services: [] },
      "/api/profile": { username: "Operator", avatar_url: null },
      "/api/alerts": { alerts: [] },
      "/api/flex-token": { remaining: 240 },
      "/api/previous-close": { closes: {} },
    };

    if (path === "/api/portfolio") {
      if (request.method() === "POST") {
        portfolioPosts += 1;
        await portfolioPostGate;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(PORTFOLIO),
        });
      }
      portfolioGets += 1;
      await portfolioGetGate;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(PORTFOLIO),
      });
    }

    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payloads[path] ?? {}),
    });
  });

  const navigation = page.goto("/portfolio", { waitUntil: "domcontentloaded" });
  await expect.poll(() => portfolioGets).toBe(1);
  await page.waitForTimeout(250);
  expect(
    portfolioPosts,
    "unknown hook state while the initial GET is pending must not trigger recovery",
  ).toBe(0);

  releasePortfolioGet();
  await navigation;
  await expect.poll(() => portfolioPosts).toBe(1);
  await expect(
    page.getByText("AAPL", { exact: true }).first(),
    "the cached snapshot must remain visible while live recovery is pending",
  ).toBeVisible();
  releasePortfolioPost();
});

test("fresh initial portfolio snapshot does not trigger recovery POST", async ({ page }) => {
  let portfolioPosts = 0;
  const freshPortfolio = { ...PORTFOLIO, last_sync: new Date().toISOString() };

  await page.route("**/api/**", (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/portfolio") {
      if (request.method() === "POST") portfolioPosts += 1;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(freshPortfolio),
      });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });

  await page.goto("/portfolio", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("AAPL", { exact: true }).first()).toBeVisible();
  await page.waitForTimeout(500);
  expect(portfolioPosts).toBe(0);
});

test("initial cached orders read paints before stale recovery can POST", async ({ page }) => {
  test.setTimeout(90_000);
  let releaseOrdersGet!: () => void;
  const ordersGetGate = new Promise<void>((resolve) => {
    releaseOrdersGet = resolve;
  });
  let releaseOrdersPost!: () => void;
  const ordersPostGate = new Promise<void>((resolve) => {
    releaseOrdersPost = resolve;
  });
  let ordersGets = 0;
  let ordersPosts = 0;
  let portfolioEntryDateGets = 0;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/orders") {
      if (request.method() === "POST") {
        ordersPosts += 1;
        await ordersPostGate;
      } else {
        ordersGets += 1;
        await ordersGetGate;
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(ORDERS),
      });
    }
    if (path === "/api/portfolio") {
      if (
        request.method() === "GET"
        && new URL(request.url()).searchParams.get("include") === "entry-dates"
      ) {
        portfolioEntryDateGets += 1;
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...PORTFOLIO, last_sync: new Date().toISOString() }),
      });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });

  const navigation = page.goto("/orders", { waitUntil: "domcontentloaded" });
  await expect.poll(() => ordersGets).toBe(1);
  await expect.poll(() => portfolioEntryDateGets).toBe(1);
  await page.waitForTimeout(250);
  expect(
    ordersPosts,
    "unknown hook state while the initial orders GET is pending must not trigger recovery",
  ).toBe(0);

  releaseOrdersGet();
  await navigation;
  await expect.poll(() => ordersPosts).toBe(1);
  await expect(
    page.getByTestId("orders-command-strip"),
    "the cached orders snapshot must remain visible while live recovery is pending",
  ).toBeVisible();
  releaseOrdersPost();
});
