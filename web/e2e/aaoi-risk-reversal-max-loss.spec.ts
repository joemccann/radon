/**
 * E2E regression: AAOI Risk Reversal max-loss (2026-05-19 P0 bug)
 *
 * The user built a Risk Reversal on AAOI in the order builder:
 *   - SELL 50 × $150 Put @ $23.65 (short put → naked)
 *   - BUY  50 × $200 Call @ $25.25
 *   - Net debit: ~$1 per share × 50 × 100 = $5,000
 *
 * Pre-fix the confirmation summary read:
 *   Total: $5,000  /  Max Gain: $245,000  /  Max Loss: $5,000  ← WRONG
 *
 * The short put alone carries 150 × 50 × 100 = $750,000 of assignment risk.
 * The corrected formula renders ~$755,000 of max loss (assignment + debit)
 * AND surfaces an "Undefined risk" Gate 1 warning, since CLAUDE.md requires
 * defined-risk only.
 *
 * This spec drives the chain UI, builds the structure, opens the confirm
 * step, and asserts both the corrected dollar number and the warning.
 */

import { test, expect } from "@playwright/test";

const EXPIRY_RAW = "2026-06-19";
const EXPIRY_COMPACT = "20260619";

const PORTFOLIO_EMPTY = {
  bankroll: 1_000_000,
  peak_value: 1_000_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [],
};

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const EXPIRATIONS = {
  symbol: "AAOI",
  expirations: [EXPIRY_COMPACT],
};

const CHAIN_STRIKES = {
  symbol: "AAOI",
  expiry: EXPIRY_COMPACT,
  exchange: "SMART",
  strikes: [140, 150, 160, 170, 180, 190, 200, 210],
  multiplier: "100",
};

const TICKER_FIXTURE = {
  uw_info: { name: "Applied Optoelectronics", sector: "Tech", description: "Test" },
  stock_state: {},
  profile: {},
  stats: {},
};

const REGIME_FIXTURE = { score: 15, cri: { score: 15 } };
const IB_STATUS_FIXTURE = { connected: true };
const BLOTTER_EMPTY = {
  as_of: new Date().toISOString(),
  summary: { realized_pnl: 0 },
  closed_trades: [],
  open_trades: [],
};

const PRICE_FIXTURES = {
  AAOI: {
    symbol: "AAOI",
    last: 175.0,
    lastIsCalculated: false,
    bid: 174.95,
    ask: 175.05,
    bidSize: 100,
    askSize: 100,
    volume: 1_000_000,
    high: null,
    low: null,
    open: null,
    close: 174.0,
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
  AAOI_20260619_150_P: {
    symbol: "AAOI_20260619_150_P",
    last: 23.65,
    lastIsCalculated: false,
    bid: 23.5,
    ask: 23.8,
    bidSize: 30,
    askSize: 30,
    volume: 250,
    high: null,
    low: null,
    open: null,
    close: 23.4,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: -0.45,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.65,
    undPrice: 175.0,
    timestamp: new Date().toISOString(),
  },
  AAOI_20260619_200_C: {
    symbol: "AAOI_20260619_200_C",
    last: 25.25,
    lastIsCalculated: false,
    bid: 25.1,
    ask: 25.4,
    bidSize: 25,
    askSize: 25,
    volume: 100,
    high: null,
    low: null,
    open: null,
    close: 25.0,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: 0.45,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.6,
    undPrice: 175.0,
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

async function stubApis(page: import("@playwright/test").Page) {
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REGIME_FIXTURE) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(IB_STATUS_FIXTURE) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(BLOTTER_EMPTY) }),
  );
  await page.route("**/api/ticker/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TICKER_FIXTURE) }),
  );
  await page.route("**/api/options/expirations*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(EXPIRATIONS) }),
  );
  await page.route("**/api/options/chain*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CHAIN_STRIKES) }),
  );
}

test.describe("AAOI Risk Reversal — corrected Max Loss + Gate 1 warning", () => {
  test("naked short put leg drives max loss to ~$755k, not the $5k net debit", async ({ page }) => {
    await installMockWebSocket(page);
    await stubApis(page);

    await page.goto("/AAOI?tab=chain");

    const detail = page.locator(".ticker-detail-page");
    await detail.waitFor({ timeout: 10_000 });
    await detail.locator(".chain-grid").waitFor();

    // Build the Risk Reversal:
    //   1. Click the $150 put BID (sells the put)
    //   2. Click the $200 call ASK (buys the call)
    const put150Row = detail.getByRole("row", { name: /\$150\.00/ }).first();
    await put150Row.waitFor({ timeout: 5_000 });
    // Two .chain-bid cells per row (call side + put side). Put bid is the
    // second one in DOM order; selling the put = click its bid.
    const putBidCell = put150Row.locator(".chain-bid.chain-clickable").nth(1);
    await putBidCell.click();

    const call200Row = detail.getByRole("row", { name: /\$200\.00/ }).first();
    await call200Row.waitFor({ timeout: 5_000 });
    const callAskCell = call200Row.locator(".chain-ask.chain-clickable").nth(0);
    await callAskCell.click();

    const orderBuilder = detail.locator(".order-builder");
    await expect(orderBuilder).toBeVisible();

    // Set quantity to 50 contracts on each leg. The OrderBuilder shows a
    // quantity field per leg.
    const qtyInputs = orderBuilder.getByRole("spinbutton", { name: "Quantity", exact: true });
    const qtyCount = await qtyInputs.count();
    expect(qtyCount).toBeGreaterThanOrEqual(2);
    for (let i = 0; i < qtyCount; i++) {
      await qtyInputs.nth(i).fill("50");
    }

    // Pin the quoted scenario's $1 net debit without overwriting leg prices.
    await orderBuilder.locator(".order-builder-limit-field input").fill("1.00");

    // Review switches to confirmation without transmitting an order.
    const placeBtn = orderBuilder.getByRole("button", { name: /Verify order/i }).first();
    await expect(placeBtn).toBeEnabled();
    await placeBtn.click();

    // Confirm step is rendered with the order summary.
    const summary = orderBuilder.locator(".order-confirm-summary");
    await expect(summary).toBeVisible();

    // Gate 1 warning must surface — naked short put driving undefined risk.
    const warning = summary.locator('[data-testid="order-undefined-risk-warning"]');
    await expect(warning).toBeVisible();
    await expect(warning).toContainText(/GATE 1/i);
    await expect(warning).toContainText(/short put/i);

    // The Max Loss displayed must be a six-figure dollar number (the strike-
    // to-zero stress), not the $5,000 net debit.
    const metricLabels = summary.locator(".order-confirm-metric-label");
    const maxLossLabel = metricLabels.filter({ hasText: /Max Loss/ }).first();
    await expect(maxLossLabel).toBeVisible();
    const maxLossValue = maxLossLabel.locator("xpath=following-sibling::span[1]");
    const maxLossText = (await maxLossValue.textContent()) ?? "";
    // Strip currency formatting and assert the dollar magnitude.
    const maxLossDollars = Number(maxLossText.replace(/[^0-9.-]/g, ""));
    expect(maxLossDollars).toBe(755_000);
    // Sanity: the pre-fix value of $5,000 must NOT appear here.
    expect(maxLossDollars).not.toBe(5000);
  });
});
