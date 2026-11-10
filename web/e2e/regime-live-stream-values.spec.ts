import { expect, test } from "@playwright/test";

const CRI_MOCK_OPEN = {
  scan_time: "2026-03-12T10:00:00",
  market_open: true,
  date: "2026-03-12",
  vix: 24.1,
  vvix: 112.4,
  spy: 557.0,
  vix_5d_roc: 4.2,
  vvix_vix_ratio: 4.66,
  realized_vol: 12.3,
  cor1m: 29.9,
  cor1m_previous_close: 30.9,
  cor1m_5d_change: 1.3,
  spx_100d_ma: 555.0,
  spx_distance_pct: 0.36,
  spy_closes: Array.from({ length: 22 }, (_, i) => 545 + i * 0.5),
  cri: { score: 18, level: "LOW", components: { vix: 4, vvix: 5, correlation: 4, momentum: 5 } },
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false },
  },
  cta: { exposure_pct: 90, forced_reduction_pct: 0, est_selling_bn: 0 },
  menthorq_cta: null,
  history: [{ date: "2026-03-11", vix: 24.0, vvix: 111.5, spy: 556.2, cor1m: 30.1, realized_vol: 12.0, spx_vs_ma_pct: 0.2, vix_5d_roc: 3.9 }],
};

const LIVE_BATCH = {
  VIX: {
    symbol: "VIX",
    last: 26.4,
    lastIsCalculated: false,
    bid: 26.3,
    ask: 26.5,
    bidSize: null,
    askSize: null,
    volume: null,
    high: 27.1,
    low: 25.4,
    open: 25.2,
    close: 24.8,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-12T14:35:00.000Z",
  },
  VVIX: {
    symbol: "VVIX",
    last: 118.2,
    lastIsCalculated: false,
    bid: 118.0,
    ask: 118.4,
    bidSize: null,
    askSize: null,
    volume: null,
    high: 121.2,
    low: 116.7,
    open: 117.0,
    close: 120.4,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-12T14:35:00.000Z",
  },
  SPY: {
    symbol: "SPY",
    last: 561.5,
    lastIsCalculated: false,
    bid: 561.4,
    ask: 561.6,
    bidSize: 100,
    askSize: 100,
    volume: 1000,
    high: 562.0,
    low: 556.9,
    open: 557.1,
    close: 557.2,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-12T14:35:00.000Z",
  },
  COR1M: {
    symbol: "COR1M",
    last: 31.25,
    lastIsCalculated: false,
    bid: 31.2,
    ask: 31.3,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: 29.8,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: "2026-03-12T14:35:00.000Z",
  },
};

async function setupMocks(page: import("@playwright/test").Page) {
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

  await page.addInitScript((batch) => {
    class MockWebSocket {
      public url: string;
      public readyState = 0;
      public onopen: ((event: Event) => void) | null = null;
      public onmessage: ((event: MessageEvent<string>) => void) | null = null;
      public onclose: ((event: Event) => void) | null = null;
      public onerror: ((event: Event) => void) | null = null;

      constructor(url: string) {
        this.url = url;
        window.setTimeout(() => {
          this.readyState = 1;
          this.onopen?.(new Event("open"));
        }, 0);
        window.setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({ type: "status", ib_connected: true, subscriptions: ["VIX", "VVIX", "SPY", "COR1M"] }),
          } as MessageEvent<string>);
        }, 10);
        window.setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({ type: "batch", updates: batch }),
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
}

test.describe("/regime page — live index stream values", () => {
  test("renders live VIX, VVIX, and COR1M strip values from websocket batch updates", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const vixCell = page.locator('[data-testid="strip-vix"]');
    const vvixCell = page.locator('[data-testid="strip-vvix"]');
    const cor1mCell = page.locator('[data-testid="strip-cor1m"]');

    await expect(vixCell.locator(".regime-strip-value")).toHaveText("26.40");
    await expect(vvixCell.locator(".regime-strip-value")).toHaveText("118.20");
    await expect(cor1mCell.locator(".regime-strip-value")).toHaveText("31.25");

    await expect(vixCell.locator(".regime-badge")).toHaveText("LIVE");
    await expect(vvixCell.locator(".regime-badge")).toHaveText("LIVE");
    await expect(cor1mCell.locator(".regime-badge")).toHaveText("LIVE");

    await expect(vixCell.locator(".regime-strip-ts")).not.toHaveText("---");
    await expect(vvixCell.locator(".regime-strip-ts")).not.toHaveText("---");
    await expect(cor1mCell.locator('[data-testid="regime-day-chg"]')).toContainText("+0.35 (+1.13%)");
  });
});
