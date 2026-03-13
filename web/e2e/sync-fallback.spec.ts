import { expect, test } from "@playwright/test";

/**
 * E2E: When IB sync POST endpoints fail, the UI should still render
 * portfolio and orders data from the cached fallback (not show errors).
 */

const PORTFOLIO_MOCK = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: "2026-03-13T14:00:00Z",
  positions: [
    {
      id: 1,
      ticker: "AAPL",
      structure: "Long Call",
      structure_type: "Long Call",
      risk_profile: "defined",
      expiry: "2026-04-17",
      contracts: 10,
      direction: "LONG",
      entry_cost: 5_000,
      max_risk: 5_000,
      market_value: 6_000,
      legs: [
        {
          direction: "LONG",
          contracts: 10,
          type: "Call",
          strike: 270,
          entry_cost: 5_000,
          avg_cost: 500,
          market_price: 6.0,
          market_value: 6_000,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-10",
    },
  ],
  total_deployed_pct: 5,
  total_deployed_dollars: 5_000,
  remaining_capacity_pct: 95,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  account_summary: {
    net_liquidation: 100_000,
    daily_pnl: 200,
    unrealized_pnl: 1_000,
    realized_pnl: 0,
    settled_cash: 95_000,
    maintenance_margin: 3_000,
    excess_liquidity: 92_000,
    buying_power: 200_000,
    dividends: 0,
  },
};

const ORDERS_MOCK = {
  last_sync: "2026-03-13T14:00:00Z",
  open_orders: [
    {
      orderId: 1,
      permId: 12345,
      symbol: "AAPL",
      contract: {
        conId: 100,
        symbol: "AAPL",
        secType: "OPT",
        strike: 270,
        right: "C",
        expiry: "2026-04-17",
      },
      action: "BUY",
      orderType: "LMT",
      totalQuantity: 10,
      limitPrice: 5.0,
      auxPrice: 0,
      status: "Submitted",
      filled: 0,
      remaining: 10,
      avgFillPrice: 0,
      tif: "GTC",
    },
  ],
  executed_orders: [],
  open_count: 1,
  executed_count: 0,
};

const CRI_MOCK = {
  scan_time: "2026-03-13T10:00:00",
  market_open: true,
  date: "2026-03-13",
  vix: 20,
  vvix: 100,
  spy: 560,
  vix_5d_roc: 1.0,
  vvix_vix_ratio: 5.0,
  realized_vol: 12,
  cor1m: 30,
  cor1m_previous_close: 29,
  cor1m_5d_change: 1.0,
  spx_100d_ma: 550,
  spx_distance_pct: 1.8,
  spy_closes: Array.from({ length: 22 }, (_, i) => 555 + i * 0.3),
  cri: { score: 15, level: "LOW", components: { vix: 4, vvix: 3, correlation: 4, momentum: 4 } },
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false },
  },
  cta: { exposure_pct: 95, forced_reduction_pct: 0, est_selling_bn: 0 },
  menthorq_cta: null,
  history: [],
};

async function setupSyncFallbackMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  // Portfolio GET returns cached data; POST also returns cached data (simulating fallback)
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_MOCK),
      headers: { "X-Sync-Warning": "IB sync failed - serving cached data" },
    }),
  );

  // Orders GET returns cached data; POST also returns cached data (simulating fallback)
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_MOCK),
      headers: { "X-Sync-Warning": "IB sync failed - serving cached data" },
    }),
  );

  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CRI_MOCK) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/menthorq/cta", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tables: [] }) }),
  );
  await page.route("**/api/previous-close", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ closes: {} }) }),
  );

  // Mock WebSocket to prevent real connections
  await page.addInitScript(() => {
    class MockWebSocket {
      public static OPEN = 1;
      public url: string;
      public readyState = 0;
      public onopen: ((event: Event) => void) | null = null;
      public onmessage: ((event: MessageEvent<string>) => void) | null = null;
      public onclose: ((event: Event) => void) | null = null;
      public onerror: ((event: Event) => void) | null = null;

      constructor(url: string) {
        this.url = url;
        window.setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.(new Event("open"));
        }, 0);
        window.setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({
              type: "status",
              ib_connected: true,
              ib_issue: null,
              ib_status_message: null,
              subscriptions: [],
            }),
          } as MessageEvent<string>);
        }, 10);
      }

      send(_message: string) {}

      close() {
        this.readyState = 3;
        this.onclose?.(new Event("close"));
      }
    }

    Object.defineProperty(window, "WebSocket", {
      configurable: true,
      writable: true,
      value: MockWebSocket,
    });
  });
}

test.describe("Sync fallback — UI resilience when IB sync fails", () => {
  test("portfolio page renders positions from cached data when sync POST falls back", async ({ page }) => {
    await setupSyncFallbackMocks(page);
    await page.goto("/portfolio");

    // Portfolio data should render (AAPL position visible in Defined Risk table)
    await expect(page.getByText("AAPL")).toBeVisible({ timeout: 10_000 });

    // No console 502 errors — the response is 200 with cached data
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error" && msg.text().includes("502")) {
        consoleErrors.push(msg.text());
      }
    });

    // Wait a sync cycle
    await page.waitForTimeout(2_000);
    expect(consoleErrors).toHaveLength(0);
  });

  test("orders page renders open orders from cached data when sync POST falls back", async ({ page }) => {
    await setupSyncFallbackMocks(page);
    await page.goto("/orders");

    // Orders data should render (AAPL order visible)
    await expect(page.getByText("AAPL")).toBeVisible({ timeout: 10_000 });

    // No 502 errors in console
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error" && msg.text().includes("502")) {
        consoleErrors.push(msg.text());
      }
    });

    await page.waitForTimeout(2_000);
    expect(consoleErrors).toHaveLength(0);
  });

  test("no error toast or error banner shown when serving cached data on sync failure", async ({ page }) => {
    await setupSyncFallbackMocks(page);
    await page.goto("/portfolio");

    // Wait for data to render
    await expect(page.getByText("AAPL")).toBeVisible({ timeout: 10_000 });

    // Sync status should NOT show "Sync error" since the response is 200
    const syncError = page.locator(".sync-status.sync-error");
    await expect(syncError).toHaveCount(0);
  });
});
