import { expect, test } from "@playwright/test";

/**
 * Overnight long put + same-day short put grouped as a bull put spread.
 * Today P&L must not equal the long put's accumulated total P&L.
 */
const TODAY = "2026-09-17";

const PORTFOLIO_MOCK = {
  bankroll: 1_000_000,
  peak_value: 1_000_000,
  last_sync: `${TODAY}T16:24:08Z`,
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 1_000_000,
    daily_pnl: -550,
    unrealized_pnl: -162085.04,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 400_000,
    dividends: 0,
  },
  positions: [
    {
      id: 8,
      ticker: "SPY",
      structure: "Bull Put Spread $740.0/$760.0",
      structure_type: "Bull Put Spread",
      risk_profile: "defined",
      expiry: "2026-09-18",
      contracts: 50,
      direction: "DEBIT",
      entry_cost: null,
      max_risk: null,
      market_value: -10000,
      market_price_is_calculated: false,
      ib_daily_pnl: null,
      basis_source: "mixed",
      entry_date: TODAY,
      kelly_optimal: null,
      target: null,
      stop: null,
      legs: [
        {
          direction: "LONG",
          contracts: 50,
          type: "Put",
          strike: 740.0,
          entry_cost: 162535.04,
          avg_cost: 3250.70075,
          market_price: 0.12,
          market_value: 600.0,
          market_price_is_calculated: false,
          basis_source: "ib",
        },
        {
          direction: "SHORT",
          contracts: 50,
          type: "Put",
          strike: 760.0,
          entry_cost: 10450.0,
          avg_cost: 209.0,
          market_price: 2.12,
          market_value: 10600.0,
          market_price_is_calculated: false,
          basis_source: "session_fills",
        },
      ],
    },
  ],
};

const ORDERS_EMPTY = {
  last_sync: `${TODAY}T16:24:08Z`,
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

async function freezeToTradingDay(page: import("@playwright/test").Page) {
  await page.addInitScript((iso) => {
    const fixedNow = new Date(iso).valueOf();
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
  }, `${TODAY}T16:24:08Z`);
}

async function installMockWebSocket(page: import("@playwright/test").Page) {
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

    const NativeWebSocket = window.WebSocket;
    const RelayAwareWebSocket = function (url: string | URL, protocols?: string | string[]) {
      return !String(url).includes("/_next/")
        ? (new MockWebSocket(String(url)) as unknown as WebSocket)
        : new NativeWebSocket(url, protocols);
    } as unknown as typeof WebSocket;
    Object.assign(RelayAwareWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    Object.defineProperty(window, "WebSocket", {
      configurable: true,
      writable: true,
      value: RelayAwareWebSocket,
    });
  });
}

async function setupMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("https://**", (route) => route.abort());
  await page.route("**/api/**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await freezeToTradingDay(page);
  await installMockWebSocket(page);

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_MOCK) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        scan_time: `${TODAY}T16:24:08Z`,
        market_open: true,
        date: TODAY,
        vix: 16.0,
        vvix: 90.0,
        spy: 762.27,
        vix_5d_roc: 0,
        vvix_vix_ratio: 5.6,
        realized_vol: 11.0,
        cor1m: 30.0,
        cor1m_5d_change: 0,
        spx_100d_ma: 700,
        spx_distance_pct: 5,
        spy_closes: Array.from({ length: 22 }, (_, i) => 740 + i),
        cri: { score: 24, level: "LOW", components: { vix: 6, vvix: 5, correlation: 7, momentum: 6 } },
        cta: { exposure_pct: 95, forced_reduction_pct: 0, est_selling_bn: 1.2, realized_vol: 11.0 },
        crash_trigger: { triggered: false, conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false } },
        history: [],
      }),
    }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: `${TODAY}T16:24:08Z`, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

test.describe("/portfolio mixed-age combo today pnl", () => {
  test("does not print the overnight long put's total loss as Today P&L", async ({ page }, testInfo) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    const row = page.locator("tr", { hasText: "SPY" }).first();
    await expect(row).toContainText("Bull Put Spread");

    const todayCell = row.getByTestId("position-cell-today-pnl");
    const pnlCell = row.getByTestId("position-cell-pnl");

    // No WS closes: an unmeasured overnight leg withholds the position total.
    await page.screenshot({ path: testInfo.outputPath("mixed-coverage-desktop.png"), fullPage: true });
    await expect(todayCell).toHaveText("—");
    // Total P&L is still the accumulated long-put loss plus the short's −$150.
    await expect(pnlCell).toHaveText("-$162,085");
    await expect(todayCell).not.toHaveText("-$162,085");
  });
});


test("mobile withholds the mixed-age total with an unmeasured overnight leg", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setupMocks(page);
  await page.goto("/portfolio");
  const card = page.getByTestId("mobile-position-SPY");
  await expect(card).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("mixed-coverage-mobile.png"), fullPage: true });
  await expect(card.getByTestId("mobile-position-today")).toContainText("—");
  await expect(card.getByTestId("mobile-position-today")).not.toContainText("150");
});
