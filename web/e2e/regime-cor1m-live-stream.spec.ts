import { test, expect } from "@playwright/test";

const CRI_MOCK_OPEN = {
  scan_time: "2026-03-11T10:00:00",
  market_open: true,
  date: "2026-03-10",
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
  history: [],
};

const LIVE_COR1M_PRICE = {
  symbol: "COR1M",
  last: 29.31,
  lastIsCalculated: false,
  bid: 29.3,
  ask: 29.32,
  bidSize: null,
  askSize: null,
  volume: null,
  high: null,
  low: null,
  open: null,
  close: 31.1,
  week52High: null,
  week52Low: null,
  avgVolume: null,
  delta: null,
  gamma: null,
  theta: null,
  vega: null,
  impliedVol: null,
  undPrice: null,
  timestamp: "2026-03-11T10:05:00.000Z",
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

  await page.addInitScript((priceData) => {
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
            data: JSON.stringify({ type: "status", ib_connected: true, subscriptions: ["COR1M"] }),
          } as MessageEvent<string>);
        }, 10);
        window.setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({ type: "price", symbol: "COR1M", data: priceData }),
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
  }, LIVE_COR1M_PRICE);
}

test.describe("/regime page — live COR1M stream", () => {
  test("uses the prior CRI/Cboe close for COR1M day-over-day instead of the IB close field", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime");

    const cor1mCell = page.locator('[data-testid="strip-cor1m"]');
    await cor1mCell.waitFor({ timeout: 10_000 });

    await expect(cor1mCell.locator(".regime-strip-value")).toHaveText("29.31");
    await expect(cor1mCell.locator(".regime-badge")).toHaveText("LIVE");
    await expect(cor1mCell.locator('[data-testid="regime-day-chg"]')).toContainText("+0.34 (+1.17%)");
    await expect(cor1mCell.locator(".regime-strip-sub")).toHaveText("5d chg: +1.48 pts");
  });
});
