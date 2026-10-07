import { expect, test } from "@playwright/test";

function parsePrice(text: string | null): number {
  return Number((text ?? "").replace(/[$,]/g, ""));
}

function formatUsd(value: number): string {
  return `$${value.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

const PORTFOLIO_WITH_SINGLE_CALL = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 22.78,
  total_deployed_dollars: 22_775,
  remaining_capacity_pct: 77.22,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  risk_budget: {
    clusters: [],
    breaches: [],
    aggregate_exposure: 0,
    insufficient_data: [],
    corr_threshold: 0.7,
    book_budget: 0.025,
  },
  positions: [
    {
      id: 1,
      ticker: "AAOI",
      structure: "Long Call $105",
      structure_type: "Long Call",
      risk_profile: "defined",
      expiry: "2026-03-20",
      contracts: 25,
      direction: "LONG",
      entry_cost: 22_775,
      max_risk: 22_775,
      market_value: 36_675,
      market_price: 14.67,
      market_price_is_calculated: false,
      legs: [
        {
          direction: "LONG",
          contracts: 25,
          type: "Call",
          strike: 105,
          entry_cost: 22_775,
          avg_cost: 911,
          market_price: 14.67,
          market_price_is_calculated: false,
          market_value: 36_675,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-05",
    },
  ],
};

const ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 101,
      permId: 202,
      symbol: "AAOI",
      contract: {
        conId: 123456,
        symbol: "AAOI",
        secType: "OPT",
        strike: 105,
        right: "C",
        expiry: "2026-03-20",
      },
      action: "BUY",
      orderType: "LMT",
      totalQuantity: 25,
      limitPrice: 11.95,
      auxPrice: null,
      status: "Submitted",
      filled: 0,
      remaining: 25,
      avgFillPrice: null,
      tif: "DAY",
    },
  ],
  executed_orders: [],
  open_count: 1,
  executed_count: 0,
};

const PRICES = {
  AAOI: {
    symbol: "AAOI",
    last: 19.6,
    lastIsCalculated: false,
    bid: 19.55,
    ask: 19.65,
    bidSize: 20,
    askSize: 15,
    volume: 500_000,
    high: 20.3,
    low: 19.1,
    open: 19.25,
    close: 19.0,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: new Date().toISOString(),
  },
  AAOI_20260320_105_C: {
    symbol: "AAOI_20260320_105_C",
    last: 13.6,
    lastIsCalculated: false,
    bid: 12.8,
    ask: 14.4,
    bidSize: 10,
    askSize: 12,
    volume: 232,
    high: 15.89,
    low: 12.4,
    open: 15.1,
    close: 15.16,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.48,
    gamma: 0.04,
    theta: -0.19,
    vega: 0.11,
    impliedVol: 0.54,
    undPrice: 19.6,
    timestamp: new Date().toISOString(),
  },
};

function stubApis(page: import("@playwright/test").Page) {
  page.route("**/api/portfolio**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_WITH_SINGLE_CALL),
    }),
  );

  page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS),
    }),
  );

  page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ score: 15, cri: { score: 15 } }),
    }),
  );

  page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: false }),
    }),
  );

  page.route("**/api/blotter", (route) =>
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

  page.route("**/api/ticker/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        uw_info: { name: "Applied Optoelectronics, Inc.", sector: "Technology", description: "Test" },
        stock_state: {},
        profile: {},
        stats: {},
      }),
    }),
  );

  page.route("**/api/prices", (route) => route.abort());
}

async function installMockWebSocket(page: import("@playwright/test").Page) {
  await page.addInitScript((fixtures) => {
    class MockWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
      readyState = MockWebSocket.CONNECTING;
      onopen: ((event?: unknown) => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onclose: ((event?: unknown) => void) | null = null;
      onerror: ((event?: unknown) => void) | null = null;
      constructor() {
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.({});
          this.onmessage?.({ data: JSON.stringify({ type: "status", ib_connected: true, subscriptions: [] }) });
          this.onmessage?.({ data: JSON.stringify({ type: "batch", updates: fixtures }) });
        }, 0);
      }
      send() {}
      close() { this.readyState = MockWebSocket.CLOSED; this.onclose?.({}); }
    }
    // @ts-expect-error test-only replacement
    window.WebSocket = MockWebSocket;
  }, PRICES);
}

test.describe("Modify-order spread telemetry", () => {
  test("shows raw spread dollars and midpoint percent in the modify modal", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await page.route(/^https?:\/\/(?!localhost:|127\.0\.0\.1:)/, (route) => route.abort());
    await page.route("**/api/**", (route) => route.fulfill({ status: 503, body: "Unmocked API blocked by test fixture" }));
    await installMockWebSocket(page);
    stubApis(page);

    await page.goto("/orders");
    const row = page.getByRole("row", { name: /AAOI/ }).first();
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "MODIFY" }).click();

    const modifyModal = page.locator(".modify-dialog");
    await modifyModal.waitFor({ timeout: 5_000 });

    const bidText = await modifyModal
      .locator(".price-bar-item")
      .filter({ hasText: "BID" })
      .locator(".price-bar-value")
      .textContent();
    const askText = await modifyModal
      .locator(".price-bar-item")
      .filter({ hasText: "ASK" })
      .locator(".price-bar-value")
      .textContent();
    const spreadValue = modifyModal
      .locator(".price-bar-item")
      .filter({ hasText: "SPREAD" })
      .locator(".price-bar-value");

    const bid = parsePrice(bidText);
    const ask = parsePrice(askText);
    const fullSpread = ask - bid;
    const mid = (bid + ask) / 2;
    const expectedSpread = `${formatUsd(fullSpread)} / ${((fullSpread / mid) * 100).toFixed(2)}%`;

    await expect(spreadValue).toHaveText(expectedSpread);
  });
});
