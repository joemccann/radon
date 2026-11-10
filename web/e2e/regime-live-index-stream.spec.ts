import { test, expect } from "@playwright/test";

const CRI_MOCK_OPEN = {
  scan_time: "2026-03-12T10:00:00",
  market_open: true,
  date: "2026-03-12",
  vix: 24.0,
  vvix: 115.0,
  spy: 555.0,
  vix_5d_roc: 5.2,
  vvix_vix_ratio: 4.79,
  realized_vol: 12.5,
  cor1m: 29.31,
  cor1m_previous_close: 28.97,
  cor1m_5d_change: 1.48,
  spx_100d_ma: 560.0,
  spx_distance_pct: -0.89,
  spy_closes: Array.from({ length: 22 }, (_, i) => 550 + i * 0.5),
  cri: { score: 20, level: "LOW", components: { vix: 5, vvix: 4, correlation: 6, momentum: 5 } },
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false },
  },
  cta: { exposure_pct: 95, forced_reduction_pct: 0, est_selling_bn: 0 },
  menthorq_cta: null,
  history: [{ date: "2026-03-12", vix: 24.0, vvix: 115.0, spy: 555.0, cor1m: 29.31 }],
};

const LIVE_BATCH = {
  VIX: {
    symbol: "VIX",
    last: 25.5,
    lastIsCalculated: false,
    bid: 25.4,
    ask: 25.6,
    bidSize: null,
    askSize: null,
    volume: null,
    high: 26.1,
    low: 24.7,
    open: 24.8,
    close: 24.0,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-13T18:05:00.000Z",
  },
  VVIX: {
    symbol: "VVIX",
    last: 110.0,
    lastIsCalculated: false,
    bid: 109.5,
    ask: 110.5,
    bidSize: null,
    askSize: null,
    volume: null,
    high: 111.5,
    low: 108.8,
    open: 109.2,
    close: 115.0,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-13T18:05:00.000Z",
  },
  COR1M: {
    symbol: "COR1M",
    last: 31.44,
    lastIsCalculated: false,
    bid: 31.4,
    ask: 31.48,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: 29.56,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-13T18:05:00.000Z",
  },
  SPY: {
    symbol: "SPY",
    last: 560.25,
    lastIsCalculated: false,
    bid: 560.1,
    ask: 560.4,
    bidSize: 100,
    askSize: 100,
    volume: 1000,
    high: 561.0,
    low: 554.0,
    open: 555.2,
    close: 555.1,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-13T18:05:00.000Z",
  },
};

async function setupMocks(page: import("@playwright/test").Page) {
  let previousCloseRequests = 0;

  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CRI_MOCK_OPEN) }),
  );
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ bankroll: 100_000, positions: [], account_summary: {}, exposure: {}, violations: [] }) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ last_sync: new Date().toISOString(), open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 }) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  await page.route("**/api/menthorq/cta", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tables: [] }) }),
  );
  await page.route("**/api/previous-close", async (route) => {
    previousCloseRequests += 1;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ closes: {} }) });
  });

  await page.addInitScript((liveBatch) => {
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
              subscriptions: ["SPY", "VIX", "VVIX", "COR1M"],
            }),
          } as MessageEvent<string>);
        }, 10);
        window.setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({ type: "batch", updates: liveBatch }),
          } as MessageEvent<string>);
        }, 25);
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
  }, LIVE_BATCH);

  return {
    getPreviousCloseRequests: () => previousCloseRequests,
  };
}

test.describe("/regime page — live websocket index stream", () => {
  test("replaces cached CRI values with live batch prices for VIX, VVIX, and COR1M", async ({ page }) => {
    await page.clock.install({ time: new Date("2026-03-13T18:10:00.000Z") });
    const tracker = await setupMocks(page);
    await page.goto("/regime");

    const vixCell = page.locator('[data-testid="strip-vix"]');
    const vvixCell = page.locator('[data-testid="strip-vvix"]');
    const cor1mCell = page.locator('[data-testid="strip-cor1m"]');

    await vixCell.waitFor({ timeout: 10_000 });

    await expect(vixCell.locator(".regime-strip-value")).toHaveText("25.50");
    await expect(vvixCell.locator(".regime-strip-value")).toHaveText("110.00");
    await expect(cor1mCell.locator(".regime-strip-value")).toHaveText("31.44");

    await expect(vixCell.locator(".regime-badge")).toHaveText("LIVE");
    await expect(vvixCell.locator(".regime-badge")).toHaveText("LIVE");
    await expect(cor1mCell.locator(".regime-badge")).toHaveText("LIVE");

    await expect(vixCell.locator('[data-testid="regime-day-chg"]')).toContainText("+1.50 (+6.25%)");
    await expect(vvixCell.locator('[data-testid="regime-day-chg"]')).toContainText("-5.00 (-4.35%)");
    await expect(cor1mCell.locator('[data-testid="regime-day-chg"]')).toContainText("+2.47 (+8.53%)");

    expect(tracker.getPreviousCloseRequests()).toBe(0);
  });
});
