import { expect, test } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";

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
  history: [{ date: "2026-03-12", vix: 24.23, vvix: 122.49, spy: 555.0, cor1m: 28.97 }],
};

const LIVE_INDEX_PRICES = {
  VIX: {
    symbol: "VIX",
    last: 27.17,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: 27.17,
    low: 24.6,
    open: null,
    close: 24.23,
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
    last: 129.07,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: 129.25,
    low: 126.63,
    open: null,
    close: 122.49,
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
    last: 34.88,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: 34.88,
    low: 31.56,
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
};

async function setupMocks(page: import("@playwright/test").Page) {
  await installClearFixtures(page);

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
  await page.route("**/api/previous-close", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ closes: {} }) }),
  );
  await page.route("**/api/index-quote**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ price: null }) }),
  );

  await page.addInitScript((livePrices) => {
    class MockWebSocket {
      public static CONNECTING = 0;
      public static OPEN = 1;
      public static CLOSING = 2;
      public static CLOSED = 3;
      public url: string;
      public readyState = 0;
      public onopen: ((event: Event) => void) | null = null;
      public onmessage: ((event: MessageEvent<string>) => void) | null = null;
      public onclose: ((event: Event) => void) | null = null;
      public onerror: ((event: Event) => void) | null = null;
      private requested = new Set<string>();
      private sentPrices = false;

      constructor(url: string) {
        this.url = url;
        window.setTimeout(() => {
          this.readyState = 1;
          this.onopen?.(new Event("open"));
        }, 0);
      }

      send(message: string) {
        const parsed = JSON.parse(message) as {
          action?: string;
          indexes?: Array<{ symbol: string; exchange: string }>;
        };
        if (parsed.action !== "subscribe") return;
        const requestedIndexes = (parsed.indexes ?? []).map((entry) => entry.symbol);
        const observed = (window as Window & { __regimeIndexSubscriptions?: string[] }).__regimeIndexSubscriptions ??= [];
        observed.push(...requestedIndexes);
        for (const symbol of requestedIndexes) this.requested.add(symbol);
        if (this.sentPrices || !["VIX", "VVIX", "COR1M"].every((symbol) => this.requested.has(symbol))) return;
        this.sentPrices = true;

        window.setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({
              type: "status",
              ib_connected: true,
              subscriptions: ["VIX", "VVIX", "COR1M"],
            }),
          } as MessageEvent<string>);
        }, 10);

        for (const [index, symbol] of ["VIX", "VVIX", "COR1M"].entries()) {
          window.setTimeout(() => {
            this.onmessage?.({
              data: JSON.stringify({
                type: "price",
                symbol,
                data: livePrices[symbol as keyof typeof livePrices],
              }),
            } as MessageEvent<string>);
          }, 25 + index * 10);
        }
      }

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
  }, LIVE_INDEX_PRICES);
}

test.describe("/regime page — live index stream values", () => {
  test("subscribes to VIX, VVIX, and COR1M and renders their live websocket prices", async ({ page }) => {
    await setupMocks(page);
    await page.clock.setFixedTime(new Date("2026-03-13T18:10:00.000Z"));
    await page.goto("/regime");

    await expect(page.locator("[data-workspace-section='regime']")).toBeVisible();
    await expect.poll(() => page.evaluate(() =>
      (window as Window & { __regimeIndexSubscriptions?: string[] }).__regimeIndexSubscriptions ?? [],
    )).toEqual(expect.arrayContaining(["VIX", "VVIX", "COR1M"]));

    const vixCell = page.locator('[data-testid="strip-vix"]');
    const vvixCell = page.locator('[data-testid="strip-vvix"]');
    const cor1mCell = page.locator('[data-testid="strip-cor1m"]');

    await vixCell.waitFor({ timeout: 10_000 });

    await expect(vixCell.locator(".regime-strip-value")).toHaveText("27.17");
    await expect(vixCell.locator(".regime-badge")).toHaveText("LIVE");
    await expect(vixCell.locator('[data-testid="regime-day-chg"]')).toContainText("+2.94 (+12.13%)");

    await expect(vvixCell.locator(".regime-strip-value")).toHaveText("129.07");
    await expect(vvixCell.locator(".regime-badge")).toHaveText("LIVE");
    await expect(vvixCell.locator('[data-testid="regime-day-chg"]')).toContainText("+6.58 (+5.37%)");

    await expect(cor1mCell.locator(".regime-strip-value")).toHaveText("34.88");
    await expect(cor1mCell.locator(".regime-badge")).toHaveText("LIVE");
    await expect(cor1mCell.locator('[data-testid="regime-day-chg"]')).toContainText("+5.91 (+20.40%)");
  });
});
