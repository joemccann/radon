/**
 * E2E: Delta-adjusted leverage on the Dollar Delta card and modal
 *
 * Verifies:
 *  - The EXPOSURE row's Dollar Delta card surfaces a "+x.x% of NLV / 0.xxx multiplier"
 *    subtitle for a long-biased portfolio.
 *  - Clicking the card opens the breakdown modal with the new leverage block,
 *    showing multiplier, pct, bias label, NLV reference, and the updated formula.
 *  - A short-biased portfolio renders the negative sign and short class.
 *
 * The portfolio fixture is a single AAPL stock position (1000 shares) at $260
 * spot with NLV $1,611,889.79. dollar_delta = 1 * 1000 * 260 = $260,000 long.
 *
 * Prices arrive via the IB WebSocket. We replace `window.WebSocket` with a
 * minimal mock via `addInitScript` so we can deterministically drive the
 * "snapshot" message into usePrices without depending on the real relay.
 */

import { test, expect, type Page } from "@playwright/test";

// ── Fixtures ─────────────────────────────────────────────────────────────────

type Side = "long" | "short";
type MockSnapshot = {
  symbol: string;
  last: number;
  bid: number;
  ask: number;
  close: number;
  volume: number;
  delta?: number;
};

const NLV = 1_611_889.79;

function makePortfolio(side: Side) {
  // 1000 shares of AAPL at avg $200, market $260 → market_value = 260,000
  // Sign of contracts is positive; direction is LONG/SHORT.
  // dollar_delta = direction_sign * 1 * shares * spot
  //   long  → +260,000
  //   short → -260,000
  return {
    bankroll: NLV,
    peak_value: NLV,
    last_sync: new Date().toISOString(),
    total_deployed_pct: 16.13,
    total_deployed_dollars: 260_000,
    remaining_capacity_pct: 83.87,
    position_count: 1,
    defined_risk_count: 0,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
    positions: [
      {
        id: 1,
        ticker: "AAPL",
        structure: side === "long" ? "Stock (1000 shares LONG)" : "Stock (1000 shares SHORT)",
        structure_type: "Stock",
        risk_profile: "equity",
        direction: side === "long" ? "LONG" : "SHORT",
        contracts: 1000,
        expiry: "N/A",
        market_value: side === "long" ? 260_000 : -260_000,
        legs: [
          {
            type: "Stock",
            direction: side === "long" ? "LONG" : "SHORT",
            strike: null,
            contracts: 1000,
            avg_cost: 200_000,
            market_value: side === "long" ? 260_000 : -260_000,
          },
        ],
      },
    ],
    exposure: {},
    violations: [],
    account_summary: {
      net_liquidation: NLV,
      daily_pnl: 0,
      unrealized_pnl: 60_000,
      realized_pnl: 0,
      settled_cash: 0,
      maintenance_margin: 100_000,
      excess_liquidity: 1_000_000,
      buying_power: 4_000_000,
      dividends: 0,
    },
  };
}

function makeBearPutPortfolio() {
  return {
    bankroll: NLV,
    peak_value: NLV,
    last_sync: new Date().toISOString(),
    total_deployed_pct: 4.02,
    total_deployed_dollars: 64_750,
    remaining_capacity_pct: 95.98,
    position_count: 1,
    defined_risk_count: 1,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
    positions: [
      {
        id: 11,
        ticker: "SPY",
        structure: "Bear Put Spread $650.0/$740.0",
        structure_type: "Bear Put Spread",
        risk_profile: "defined",
        direction: "DEBIT",
        contracts: 50,
        expiry: "2026-09-18",
        market_value: 64_750,
        legs: [
          {
            type: "Put",
            direction: "SHORT",
            strike: 650,
            contracts: 50,
            avg_cost: 1026.27,
            market_value: 26_600,
          },
          {
            type: "Put",
            direction: "LONG",
            strike: 740,
            contracts: 50,
            avg_cost: 3250.7,
            market_value: 91_350,
          },
        ],
      },
    ],
    exposure: {},
    violations: [],
    account_summary: {
      net_liquidation: NLV,
      daily_pnl: 0,
      unrealized_pnl: -46_471,
      realized_pnl: 0,
      settled_cash: 0,
      maintenance_margin: 100_000,
      excess_liquidity: 1_000_000,
      buying_power: 4_000_000,
      dividends: 0,
    },
  };
}

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

// ── WebSocket mock ───────────────────────────────────────────────────────────
//
// usePrices opens ws://localhost:8765 and listens for `snapshot` / `batch`
// messages. We patch window.WebSocket before page load so the hook receives
// deterministic quote snapshots without depending on the real relay.

const AAPL_SNAPSHOTS: MockSnapshot[] = [
  { symbol: "AAPL", last: 260, bid: 259.95, ask: 260.05, close: 250, volume: 1_000_000 },
];

const BEAR_PUT_SNAPSHOTS: MockSnapshot[] = [
  { symbol: "SPY", last: 737.46, bid: 737.45, ask: 737.47, close: 740, volume: 1_000_000 },
  {
    symbol: "SPY_20260918_650_P",
    last: 6.68,
    bid: 6.65,
    ask: 6.70,
    close: 6.50,
    volume: 10_000,
    delta: 0.2468,
  },
  {
    symbol: "SPY_20260918_740_P",
    last: 24.45,
    bid: 24.40,
    ask: 24.50,
    close: 24.00,
    volume: 10_000,
    delta: 0.1623,
  },
];

async function installWsMock(page: Page, snapshots: MockSnapshot[] = AAPL_SNAPSHOTS) {
  await page.addInitScript((mockSnapshots: MockSnapshot[]) => {
    class MockWS {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;

      readyState = MockWS.CONNECTING;
      onopen: ((ev: Event) => void) | null = null;
      onmessage: ((ev: MessageEvent) => void) | null = null;
      onclose: ((ev: CloseEvent) => void) | null = null;
      onerror: ((ev: Event) => void) | null = null;
      private _listeners: Record<string, Array<(ev: Event) => void>> = {};

      constructor(_url: string) {
        setTimeout(() => {
          this.readyState = MockWS.OPEN;
          const ev = new Event("open");
          this.onopen?.(ev);
          this._listeners["open"]?.forEach((fn) => fn(ev));

          mockSnapshots.forEach((data, index) => {
            setTimeout(() => {
              const message = JSON.stringify({ type: "snapshot", symbol: data.symbol, data });
              const me = new MessageEvent("message", { data: message });
              this.onmessage?.(me);
              this._listeners["message"]?.forEach((fn) => fn(me));
            }, 50 + index * 10);
          });
        }, 10);
      }

      send(_data: string) {
        // No-op: subscriptions are accepted silently.
      }

      close() {
        this.readyState = MockWS.CLOSED;
        const ev = new CloseEvent("close");
        this.onclose?.(ev);
        this._listeners["close"]?.forEach((fn) => fn(ev as unknown as Event));
      }

      addEventListener(type: string, listener: (ev: Event) => void) {
        (this._listeners[type] ||= []).push(listener);
      }
      removeEventListener(type: string, listener: (ev: Event) => void) {
        if (!this._listeners[type]) return;
        this._listeners[type] = this._listeners[type].filter((l) => l !== listener);
      }
      dispatchEvent() {
        return true;
      }
    }
    // @ts-ignore — patching for E2E
    window.WebSocket = MockWS;
  }, snapshots);
}

async function setupMocks(page: Page, side: Side) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await installWsMock(page);

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makePortfolio(side)),
    }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_EMPTY),
    }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ score: 15, level: "LOW", cri: { score: 15 } }),
    }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: true }),
    }),
  );
  await page.route("**/api/blotter", (route) =>
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
  await page.route("**/ws-ticket", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ticket: "stub" }),
    }),
  );
}

async function setupBearPutMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await installWsMock(page, BEAR_PUT_SNAPSHOTS);

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeBearPutPortfolio()),
    }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_EMPTY),
    }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ score: 15, level: "LOW", cri: { score: 15 } }),
    }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: true }),
    }),
  );
  await page.route("**/api/blotter", (route) =>
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
  await page.route("**/ws-ticket", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ticket: "stub" }),
    }),
  );
}

async function expandExposureSection(page: Page) {
  // The EXPOSURE row is collapsed by default; open it.
  const header = page.locator(".section-label-mono", { hasText: "EXPOSURE" }).first();
  await header.waitFor({ timeout: 10_000 });
  await header.click();
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe("Dollar Delta — delta-adjusted leverage", () => {
  test("long-biased portfolio shows leverage subtitle on the card", async ({ page }) => {
    await setupMocks(page, "long");
    await page.goto("/portfolio");
    await expandExposureSection(page);

    const card = page.locator(".metric-card", { hasText: "Dollar Delta" }).first();
    await card.waitFor({ timeout: 10_000 });

    const subtitle = card.locator('[data-testid="dd-card-leverage"]');
    await subtitle.waitFor({ timeout: 5_000 });

    // 260,000 / 1,611,889.79 ≈ 16.13% → 0.16x long-biased
    await expect(card.locator('[data-testid="dd-card-leverage-pct"]')).toContainText("+16.1% of NLV");
    await expect(card.locator('[data-testid="dd-card-leverage-multiplier"]')).toContainText("0.16x");
    await expect(card.locator('[data-testid="dd-card-leverage-multiplier"]')).toContainText("long-biased");
    await expect(subtitle).toHaveClass(/metric-subtitle-long/);
  });

  test("clicking Dollar Delta card opens modal with the leverage block", async ({ page }) => {
    await setupMocks(page, "long");
    await page.goto("/portfolio");
    await expandExposureSection(page);

    const card = page.locator(".metric-card", { hasText: "Dollar Delta" }).first();
    await card.waitFor({ timeout: 10_000 });
    await card.click();

    const modal = page.locator(".modal-content");
    await modal.waitFor({ timeout: 5_000 });
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Dollar Delta");

    const block = modal.locator('[data-testid="dd-leverage-block"]');
    await expect(block).toBeVisible();
    await expect(block).toHaveClass(/dd-leverage-long/);

    await expect(modal.locator('[data-testid="dd-leverage-multiplier"]')).toContainText("0.16x");
    await expect(modal.locator('[data-testid="dd-leverage-pct"]')).toContainText("+16.1%");
    await expect(modal.locator('[data-testid="dd-leverage-bias"]')).toContainText(/long-biased/i);
    await expect(modal.locator('[data-testid="dd-leverage-nlv"]')).toContainText("$1,611,889.79");
    await expect(modal.locator('[data-testid="dd-leverage-interpretation"]')).toContainText(
      "$0.16 of directional exposure",
    );

    // Updated formula box mentions both lines.
    const formula = modal.locator(".eb-formula code");
    await expect(formula).toContainText("Dollar Delta = SUM");
    await expect(formula).toContainText("Leverage = Dollar Delta / Net Liquidation Value");
  });

  test("short-biased portfolio renders the negative leverage with the short class", async ({ page }) => {
    await setupMocks(page, "short");
    await page.goto("/portfolio");
    await expandExposureSection(page);

    const card = page.locator(".metric-card", { hasText: "Dollar Delta" }).first();
    await card.waitFor({ timeout: 10_000 });

    await expect(card.locator('[data-testid="dd-card-leverage"]')).toHaveClass(
      /metric-subtitle-short/,
    );
    await expect(card.locator('[data-testid="dd-card-leverage-pct"]')).toContainText("-16.1% of NLV");
    await expect(card.locator('[data-testid="dd-card-leverage-multiplier"]')).toContainText("-0.16x");

    await card.click();
    const modal = page.locator(".modal-content");
    await modal.waitFor({ timeout: 5_000 });

    const block = modal.locator('[data-testid="dd-leverage-block"]');
    await expect(block).toHaveClass(/dd-leverage-short/);
    await expect(modal.locator('[data-testid="dd-leverage-bias"]')).toContainText(/short-biased/i);
  });

  test("bear put spread modal expands with signed put-leg exposure", async ({ page }) => {
    await setupBearPutMocks(page);
    await page.goto("/portfolio");
    await expandExposureSection(page);

    const card = page.locator(".metric-card", { hasText: "Dollar Delta" }).first();
    await card.waitFor({ timeout: 10_000 });
    await expect(card.locator('[data-testid="dd-card-leverage-pct"]')).toContainText("-19.3% of NLV");

    await card.click();
    const modal = page.locator(".modal-content");
    await modal.waitFor({ timeout: 5_000 });

    const spreadRow = modal.locator("tr", { hasText: "Bear Put Spread $650.0/$740.0" }).first();
    await spreadRow.click();

    const shortPut = modal.locator("tr", { hasText: "SHORT 50x Put $650" }).first();
    await expect(shortPut).toContainText("+0.7532");
    await expect(shortPut).toContainText("+3766");

    const longPut = modal.locator("tr", { hasText: "LONG 50x Put $740" }).first();
    await expect(longPut).toContainText("-0.8377");
    await expect(longPut).toContainText("-4189");

    await page.screenshot({ path: "/tmp/radon-spy-bear-put-ib-leg-signs.png", fullPage: true });
  });
});
