/**
 * E2E: CRCL chain auto-focuses existing position expiry + single-leg
 * sell shows positive quote prices (regression for incident 2026-05-04).
 *
 * Bug context:
 *  1. User has long 40 CRCL $110 Call 2026-06-18. They navigate to
 *     `/CRCL?tab=chain` (no positionId in URL) intending to sell a
 *     higher-strike call against it. The chain previously defaulted to
 *     the next ≥7-day Friday expiry (20260612) — different from the
 *     user's position expiry. The fix in TickerDetailContent.tsx now
 *     passes focusPosition whenever a position exists for the ticker.
 *
 *  2. Once the user clicks SELL on a strike, the order builder shows
 *     the leg as a single short call. Previously the BID/MID/ASK quote
 *     buttons were sign-flipped negative (because computed `isDebit`
 *     said the order is a "credit"). Auto-populated limit price was
 *     also negative. The `isValidPrice` check (parsedPrice > 0 for
 *     single-leg) then rejected it → "Place Short Call" button stayed
 *     disabled. Fix in OptionsChainTab.tsx forces positive sign for
 *     single-leg quotes.
 */

import { test, expect } from "@playwright/test";

const PORTFOLIO_WITH_CRCL_LONG_CALL = {
  bankroll: 1_500_000,
  peak_value: 1_500_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 2.7,
  total_deployed_dollars: 40_028,
  remaining_capacity_pct: 97.3,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [
    {
      id: 2,
      ticker: "CRCL",
      structure: "Long Call $110.0",
      structure_type: "Long Call",
      risk_profile: "defined",
      expiry: "2026-06-18",
      contracts: 40,
      direction: "LONG",
      entry_cost: 40_027.92,
      max_risk: 40_027.92,
      market_value: 74_800,
      market_price_is_calculated: false,
      ib_daily_pnl: 0,
      legs: [
        {
          direction: "LONG",
          contracts: 40,
          type: "Call",
          strike: 110,
          entry_cost: 40_027.92,
          avg_cost: 1_000.69795,
          market_price: 18.7,
          market_value: 74_800,
          market_price_is_calculated: false,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-04-15",
    },
  ],
};

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const EXPIRATIONS = {
  symbol: "CRCL",
  // Compact YYYYMMDD format — matches what production IB API actually returns.
  // 20260612 is the closest Friday >7 days out (the previous default), 20260618
  // is the user's position expiry. The fix should select 20260618.
  expirations: ["20260508", "20260515", "20260612", "20260618", "20260717"],
};

const CHAIN_STRIKES_06_18 = {
  symbol: "CRCL",
  expiry: "20260618",
  exchange: "SMART",
  strikes: [110, 120, 130, 140, 150],
  multiplier: "100",
};

const CHAIN_STRIKES_06_12 = {
  symbol: "CRCL",
  expiry: "20260612",
  exchange: "SMART",
  strikes: [110, 120, 130, 140, 150],
  multiplier: "100",
};

const PRICE_FIXTURES = {
  CRCL: {
    symbol: "CRCL",
    last: 118.06,
    lastIsCalculated: false,
    bid: 117.95,
    ask: 118.15,
    bidSize: 100,
    askSize: 100,
    volume: 5_000_000,
    high: null,
    low: null,
    open: null,
    close: 99.7,
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
  // Long $110 (existing position) on 2026-06-18
  CRCL_20260618_110_C: {
    symbol: "CRCL_20260618_110_C",
    last: 18.7,
    lastIsCalculated: false,
    bid: 18.55,
    ask: 18.9,
    bidSize: 30,
    askSize: 30,
    volume: 250,
    high: null,
    low: null,
    open: null,
    close: 18.5,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.7,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.42,
    undPrice: 118.06,
    timestamp: new Date().toISOString(),
  },
  // Short candidate: $140 on 2026-06-18 (the leg we're selling)
  CRCL_20260618_140_C: {
    symbol: "CRCL_20260618_140_C",
    last: 8,
    lastIsCalculated: false,
    bid: 7,
    ask: 9,
    bidSize: 30,
    askSize: 30,
    volume: 100,
    high: null,
    low: null,
    open: null,
    close: 8.5,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.3,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.4,
    undPrice: 118.06,
    timestamp: new Date().toISOString(),
  },
};

function installMockWebSocket(page: import("@playwright/test").Page) {
  return page.addInitScript((priceFixtures) => {
    class MockWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
      url: string;
      readyState = MockWebSocket.CONNECTING;
      onopen: ((event?: unknown) => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onclose: ((event?: unknown) => void) | null = null;
      onerror: ((event?: unknown) => void) | null = null;
      constructor(url: string) {
        this.url = url;
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          this.onopen?.({});
          this.emit({
            type: "status",
            ib_connected: true,
            ib_issue: null,
            ib_status_message: null,
            subscriptions: [],
          });
        }, 0);
      }
      send(raw: string) {
        const message = JSON.parse(raw) as {
          action?: string;
          symbols?: string[];
          contracts?: Array<{ symbol: string; expiry: string; strike: number; right: "C" | "P" }>;
        };
        if (message.action !== "subscribe") return;
        const updates: Record<string, unknown> = {};
        for (const symbol of message.symbols ?? []) {
          if (priceFixtures[symbol]) updates[symbol] = priceFixtures[symbol];
        }
        for (const contract of message.contracts ?? []) {
          const expiry = String(contract.expiry).replace(/-/g, "");
          const key = `${String(contract.symbol).toUpperCase()}_${expiry}_${Number(contract.strike)}_${contract.right}`;
          if (priceFixtures[key]) updates[key] = priceFixtures[key];
        }
        if (Object.keys(updates).length > 0) {
          this.emit({ type: "batch", updates });
        }
      }
      close() {
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.({});
      }
      emit(payload: unknown) {
        this.onmessage?.({ data: JSON.stringify(payload) });
      }
    }
    // @ts-expect-error test-only replacement
    window.WebSocket = MockWebSocket;
  }, PRICE_FIXTURES);
}

function stubApis(page: import("@playwright/test").Page) {
  page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_WITH_CRCL_LONG_CALL) }),
  );
  page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  page.route("**/api/ticker/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        uw_info: { name: "Circle Internet Group, Inc.", sector: "Financials", description: "Test" },
        stock_state: {},
        profile: {},
        stats: {},
      }),
    }),
  );
  page.route("**/api/options/expirations*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(EXPIRATIONS) }),
  );
  page.route("**/api/options/chain*", (route) => {
    const url = new URL(route.request().url());
    const expiry = url.searchParams.get("expiry") ?? "";
    const body = expiry === "20260618" ? CHAIN_STRIKES_06_18 : CHAIN_STRIKES_06_12;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

test.describe("CRCL chain — auto-focus existing-position expiry + positive single-leg quotes", () => {
  test("chain defaults to position expiry 20260618 (not the next ≥7-day Friday 20260612)", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installMockWebSocket(page);
    stubApis(page);

    // No positionId in URL — the fix must auto-pass focusPosition based on
    // the matching ticker, not require ?positionId=2.
    await page.goto("/CRCL?tab=chain");

    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 10_000 });
    await detail.locator(".chain-grid").waitFor();

    // The chain should be on 06-18 (matching position), NOT 06-12.
    // Inspect a strike row that exists in both to confirm we got 06-18 prices.
    // Since both expiries have the same strikes in this fixture, easiest tell
    // is the "expiry: 2026-06-18" indicator somewhere on the page.
    await expect(detail).toContainText(/2026-06-18|06\/18\/2026|JUN 18/i);
  });

  test("clicking SELL on a strike shows positive BID/MID/ASK quote buttons + enabled Place button", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await installMockWebSocket(page);
    stubApis(page);

    await page.goto("/CRCL?tab=chain");

    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 10_000 });
    await detail.locator(".chain-grid").waitFor();

    // Find the $140 row and click its mid (or any clickable cell that adds
    // the leg — chain-held-leg-prices uses .chain-mid.chain-clickable).
    const row140 = detail.getByRole("row", { name: /\$140\.00/ }).first();
    await row140.waitFor({ timeout: 5_000 });

    // Click on the call side mid to add the SELL leg. Note: the chain may
    // distinguish click semantics (left vs right side, bid vs ask). For this
    // test we verify whatever leg gets added by the user's normal interaction
    // produces a positive limit price input — not a negative-signed one.
    const clickable = row140.locator(".chain-mid.chain-clickable").first();
    if (await clickable.count()) {
      await clickable.click();
    }

    const orderBuilder = detail.locator(".order-builder");
    await expect(orderBuilder).toBeVisible();

    // The auto-populated limit price input MUST be positive — not "-8.00".
    const limitInput = orderBuilder.locator('input[type="text"], input[type="number"]').filter({ hasNotText: "x" }).first();
    const limitValue = await limitInput.inputValue();
    const parsed = parseFloat(limitValue);
    expect(parsed).toBeGreaterThan(0);

    // BID/MID/ASK quote buttons should also display positive numbers.
    const bidBtn = orderBuilder.getByRole("button", { name: /^BID/i }).first();
    const bidLabel = await bidBtn.textContent();
    expect(bidLabel).not.toMatch(/-/); // no negative sign

    // Place button should be enabled (not disabled because of validation).
    const placeBtn = orderBuilder.getByRole("button", { name: /^Place/i }).first();
    await expect(placeBtn).toBeEnabled();
  });
});
