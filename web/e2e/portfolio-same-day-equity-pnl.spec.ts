import { expect, test } from "@playwright/test";

/**
 * Same-day EQUITY Today P&L. Sibling of portfolio-same-day-combo-pnl.spec.ts —
 * the entry-cost baseline shipped for options only, so three ETF positions
 * opened the same morning reported a Today P&L that contradicted their own P&L:
 *
 *   QQQ 666 sh @ $717.83 → P&L +$608 but Today P&L −$1,418 (yesterday's close)
 *
 * Position payload is the real 2026-08-11 Turso snapshot; IB's own
 * reqPnLSingle daily P&L for these rows equals MV − EC to the cent, which is
 * what the entry-baselined figure computes.
 */

const TODAY = "2026-08-11";

const PORTFOLIO_MOCK = {
  bankroll: 1_000_000,
  peak_value: 1_000_000,
  last_sync: `${TODAY}T17:57:05Z`,
  total_deployed_pct: 1.41,
  total_deployed_dollars: 1_412_056.63,
  remaining_capacity_pct: 0,
  position_count: 3,
  defined_risk_count: 3,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 1_000_000,
    daily_pnl: 1035.21,
    unrealized_pnl: 1035.21,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 400_000,
    dividends: 0,
  },
  positions: [
    stock({ id: 8, ticker: "IWM", shares: 800, entryCost: 240_801.68, marketValue: 240_808.0, avgCost: 301.00210, marketPrice: 301.01, ibDaily: 6.32 }),
    stock({ id: 9, ticker: "QQQ", shares: 666, entryCost: 478_073.05, marketValue: 478_680.84, avgCost: 717.827402, marketPrice: 718.74, ibDaily: 607.78 }),
    stock({ id: 10, ticker: "SPY", shares: 900, entryCost: 693_181.90, marketValue: 693_603.0, avgCost: 770.202111, marketPrice: 770.67, ibDaily: 421.09 }),
  ],
};

function stock(o: {
  id: number; ticker: string; shares: number; entryCost: number;
  marketValue: number; avgCost: number; marketPrice: number; ibDaily: number;
}) {
  return {
    id: o.id,
    ticker: o.ticker,
    structure: `Stock (${o.shares}.0 shares)`,
    structure_type: "Stock",
    risk_profile: "equity",
    expiry: "N/A",
    contracts: o.shares,
    direction: "LONG",
    entry_cost: o.entryCost,
    max_risk: null,
    market_value: o.marketValue,
    market_price_is_calculated: false,
    ib_daily_pnl: o.ibDaily,
    entry_date: TODAY,
    kelly_optimal: null,
    target: null,
    stop: null,
    legs: [{
      direction: "LONG",
      contracts: o.shares,
      type: "Stock",
      strike: 0.0,
      entry_cost: o.entryCost,
      avg_cost: o.avgCost,
      market_price: o.marketPrice,
      market_value: o.marketValue,
      market_price_is_calculated: false,
    }],
  };
}

/** Live quotes: `last` is the synced mark, `close` is the prior session — the
 *  baseline that produced the wrong figures before the fix. */
const QUOTES: Record<string, { last: number; close: number }> = {
  IWM: { last: 301.01, close: 299.98 },
  QQQ: { last: 718.74, close: 720.87 },
  SPY: { last: 770.67, close: 773.03 },
};

const ORDERS_EMPTY = {
  last_sync: `${TODAY}T17:57:05Z`,
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
    Object.defineProperty(window, "Date", { value: MockDate, configurable: true, writable: true });
  }, `${TODAY}T17:57:05Z`);
}

async function installMockWebSocket(
  page: import("@playwright/test").Page,
  quotes: Record<string, { last: number; close: number }>,
) {
  await page.addInitScript((quoteMap: Record<string, { last: number; close: number }>) => {
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
        // Prior-session `close` alongside `last` is what made the bug
        // reproducible: without it the stock branch simply returned null.
        window.setTimeout(() => {
          const updates: Record<string, unknown> = {};
          for (const [symbol, q] of Object.entries(quoteMap)) {
            updates[symbol] = {
              symbol, last: q.last, lastIsCalculated: false,
              bid: q.last - 0.01, ask: q.last + 0.01, bidSize: 10, askSize: 10,
              volume: 1_000_000, high: q.last, low: q.last, open: q.close, close: q.close,
              week52High: null, week52Low: null, avgVolume: null,
              delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
              timestamp: new Date().toISOString(),
            };
          }
          this.onmessage?.({ data: JSON.stringify({ type: "batch", updates }) } as MessageEvent<string>);
        }, 20);
      }

      send(_message: string) {}

      close() {
        this.readyState = 3;
        this.onclose?.(new Event("close"));
      }
    }

    // Relay socket only — replacing window.WebSocket wholesale hijacks the dev
    // server's HMR socket and stalls hydration. See
    // feedback_e2e_ws_mock_relay_only_and_44px_button_floor.
    const NativeWebSocket = window.WebSocket;
    const RelayAwareWebSocket = function (url: string | URL, protocols?: string | string[]) {
      return String(url).includes("localhost:8765")
        ? (new MockWebSocket(String(url)) as unknown as WebSocket)
        : new NativeWebSocket(url, protocols);
    } as unknown as typeof WebSocket;
    Object.assign(RelayAwareWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    Object.defineProperty(window, "WebSocket", {
      configurable: true, writable: true, value: RelayAwareWebSocket,
    });
  }, quotes);
}

async function setupMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await freezeToTradingDay(page);
  await installMockWebSocket(page, QUOTES);

  const json = (body: unknown) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/portfolio", (route) => route.fulfill(json(PORTFOLIO_MOCK)));
  await page.route("**/api/orders", (route) => route.fulfill(json(ORDERS_EMPTY)));
  await page.route("**/api/flex-token", (route) => route.fulfill(json({ ok: true, days_until_expiry: 14 })));
  await page.route("**/api/ib-status", (route) => route.fulfill(json({ connected: true })));
  await page.route("**/api/blotter", (route) =>
    route.fulfill(json({ as_of: `${TODAY}T17:57:05Z`, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] })),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

test.describe("/portfolio same-day equity today pnl", () => {
  test("equities opened today use the entry-cost baseline, not yesterday's close", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    const qqq = page.locator("tr", { hasText: "QQQ" }).first();
    await expect(qqq).toBeVisible();

    // Today P&L = Total P&L = MV − EC = 478,680.84 − 478,073.05 = +$607.79
    // → fmtUsd rounds to "+$608". Matches IB's reqPnLSingle daily (+607.78).
    await expect(qqq).toContainText("+$608");
    // Close-based figure the column used to show: (718.74 − 720.87) × 666.
    await expect(qqq).not.toContainText("-$1,419");
    // Day Chg %: 607.79 / 478,073.05 × 100 = +0.13% (was −0.30% off the close).
    await expect(qqq).toContainText("+0.13%");
    await expect(qqq).not.toContainText("-0.30%");

    const spy = page.locator("tr", { hasText: "SPY" }).first();
    // 693,603.00 − 693,181.90 = +$421.10 (IB daily: +421.09).
    await expect(spy).toContainText("+$421");
    await expect(spy).not.toContainText("-$2,124");

    const iwm = page.locator("tr", { hasText: "IWM" }).first();
    // 240,808.00 − 240,801.68 = +$6.32 (IB daily: +6.32).
    await expect(iwm).toContainText("+$6");
    await expect(iwm).not.toContainText("+$824");
  });
});
