/**
 * E2E: Risk reversal chart shows a "MIDPRICE" badge when last-trade prices
 * are absent but bid/ask quotes are available.
 *
 * Scenario:
 *   - Portfolio has a 2-leg risk reversal on AAPL (short put + long call)
 *   - WebSocket sends bid/ask for each leg but last = null
 *   - Clicking AAPL in the portfolio opens the TickerDetailModal
 *   - The PriceChart inside the modal must show the MIDPRICE badge
 *   - The chart must NOT be stuck at the mock seed price ($270)
 */

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const PORTFOLIO_WITH_RISK_REVERSAL = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 2.1,
  total_deployed_dollars: 2100,
  remaining_capacity_pct: 97.9,
  position_count: 1,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [
    {
      id: 1,
      ticker: "AAPL",
      structure: "Risk Reversal",
      structure_type: "Options",
      direction: "LONG",
      contracts: 1,
      expiry: "2026-04-17",
      entry_date: "2026-03-01",
      entry_cost: -50,        // net credit received
      market_value: null,
      market_price: null,
      market_price_is_calculated: false,
      avg_cost: -50,
      risk_profile: "undefined",
      target: null,
      stop: null,
      legs: [
        {
          direction: "SHORT",
          contracts: 1,
          type: "Put",
          strike: 220,
          avg_cost: -150,
          entry_cost: -150,
          market_price: null,
          market_price_is_calculated: false,
          market_value: null,
        },
        {
          direction: "LONG",
          contracts: 1,
          type: "Call",
          strike: 280,
          avg_cost: 100,
          entry_cost: 100,
          market_price: null,
          market_price_is_calculated: false,
          market_value: null,
        },
      ],
    },
  ],
};

/** Prices where last=null but bid/ask exist for each option leg, plus underlying. */
const PRICES_MID_ONLY = {
  // Underlying AAPL
  AAPL: {
    symbol: "AAPL",
    last: null,
    lastIsCalculated: false,
    bid: 268.50,
    ask: 268.80,
    bidSize: 5,
    askSize: 3,
    volume: 8500000,
    high: 272.10,
    low: 265.20,
    open: 267.00,
    close: 266.90,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    timestamp: new Date().toISOString(),
  },
  // Short put leg: AAPL_20260417_220_P
  "AAPL_20260417_220_P": {
    symbol: "AAPL_20260417_220_P",
    last: null,
    lastIsCalculated: false,
    bid: 1.80,
    ask: 2.10,
    bidSize: 10,
    askSize: 8,
    volume: 0,
    high: null,
    low: null,
    open: null,
    close: 1.90,
    delta: -0.12,
    gamma: 0.003,
    theta: -0.04,
    vega: 0.08,
    impliedVol: 0.28,
    undPrice: 268.65,
    timestamp: new Date().toISOString(),
  },
  // Long call leg: AAPL_20260417_280_C
  "AAPL_20260417_280_C": {
    symbol: "AAPL_20260417_280_C",
    last: null,
    lastIsCalculated: false,
    bid: 1.10,
    ask: 1.40,
    bidSize: 15,
    askSize: 12,
    volume: 0,
    high: null,
    low: null,
    open: null,
    close: 1.20,
    delta: 0.09,
    gamma: 0.002,
    theta: -0.03,
    vega: 0.07,
    impliedVol: 0.32,
    undPrice: 268.65,
    timestamp: new Date().toISOString(),
  },
};

// ---------------------------------------------------------------------------
// Helpers — mock API / WebSocket responses
// ---------------------------------------------------------------------------

function stubApis(page: import("@playwright/test").Page) {
  page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_WITH_RISK_REVERSAL),
    }),
  );

  page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        last_sync: new Date().toISOString(),
        open_orders: [],
        executed_orders: [],
        open_count: 0,
        executed_count: 0,
      }),
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
      body: JSON.stringify({}),
    }),
  );

  // Abort the real WebSocket — we'll inject prices via window injection below
  page.route("**/api/prices", (route) => route.abort());
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Risk reversal chart — mid-price fallback", () => {
  // FIXME: Needs WS mock fixture — page navigation resets React state, so
  // injected ws-price custom events no longer flow to usePrices on the ticker page.
  test.fixme("shows MIDPRICE badge when last=null but bid/ask are available", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);

    await page.goto("/portfolio");

    // Inject mid-only prices by overriding the prices state via window helper
    // (The app re-renders on price updates via usePrices hook; we simulate by
    //  dispatching a custom price message event that the hook listens to.)
    await page.evaluate((prices) => {
      // Dispatch a synthetic ws-price event for each symbol.
      // The prices WebSocket listener in usePrices dispatches "ws-price" events.
      for (const [, priceData] of Object.entries(prices)) {
        window.dispatchEvent(
          new CustomEvent("ws-price", { detail: { type: "price", symbol: (priceData as { symbol: string }).symbol, data: priceData } }),
        );
      }
    }, PRICES_MID_ONLY);

    // Open the ticker detail page for AAPL
    const aaplLink = page.locator('[aria-label="View details for AAPL"]').first();
    await aaplLink.waitFor({ timeout: 10_000 });
    await aaplLink.click();
    await page.waitForURL("**/AAPL**", { timeout: 5_000 });

    // Page should be visible
    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 5_000 });

    // Re-inject prices after page navigation (prices lost on route change)
    await page.evaluate((prices) => {
      for (const [, priceData] of Object.entries(prices)) {
        window.dispatchEvent(
          new CustomEvent("ws-price", { detail: { type: "price", symbol: (priceData as { symbol: string }).symbol, data: priceData } }),
        );
      }
    }, PRICES_MID_ONLY);

    // The MIDPRICE badge must appear in the chart area
    const midBadge = detail.locator(".price-chart-mid-badge");
    await midBadge.waitFor({ timeout: 5_000 });

    await expect(midBadge).toBeVisible();
    await expect(midBadge).toHaveText("MIDPRICE");
  });

  test("does NOT show MIDPRICE badge when last-trade prices are available", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    stubApis(page);

    await page.goto("/portfolio");

    // Inject prices WITH valid last values
    const pricesWithLast = {
      AAPL: { ...PRICES_MID_ONLY.AAPL, last: 268.65 },
    };

    await page.evaluate((prices) => {
      for (const [, priceData] of Object.entries(prices)) {
        window.dispatchEvent(
          new CustomEvent("ws-price", { detail: { type: "price", symbol: (priceData as { symbol: string }).symbol, data: priceData } }),
        );
      }
    }, pricesWithLast);

    const aaplLink = page.locator('[aria-label="View details for AAPL"]').first();
    await aaplLink.waitFor({ timeout: 10_000 });
    await aaplLink.click();
    await page.waitForURL("**/AAPL**", { timeout: 5_000 });

    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 5_000 });

    // Re-inject prices after page navigation (prices lost on route change)
    await page.evaluate((prices) => {
      for (const [, priceData] of Object.entries(prices)) {
        window.dispatchEvent(
          new CustomEvent("ws-price", { detail: { type: "price", symbol: (priceData as { symbol: string }).symbol, data: priceData } }),
        );
      }
    }, pricesWithLast);

    // Give chart time to settle (price update may arrive after mount)
    await page.waitForTimeout(500);

    // Badge must NOT be present when last price is valid
    const midBadge = detail.locator(".price-chart-mid-badge");
    await expect(midBadge).not.toBeVisible();
  });
});
