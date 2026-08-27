/**
 * E2E: a same-day position's Today P&L is its total P&L, on the phone.
 *
 * 2026-08-26, mobile /portfolio: META 40x short $580 put opened that morning
 * rendered `+$1,097` as its P&L and `-$103` as Today. The card walked the legs
 * off the raw WS `last` for its market value while `getTodayPnlDollars`
 * resolved the same leg through `resolveRealtimePrice`, which prefers the mid
 * on a wide spread. Two market values, one position, so the same-day identity
 * could not hold.
 *
 * The divergence is reproduced here without a live relay, through the same
 * seam: the synced `market_value` (-$10,920) disagrees with the leg's own mark
 * ($3.03 → -$12,120), so the card's walk and `getTodayPnlDollars` landed on
 * different market values and published +$1,097 against -$103. The wide-spread
 * variant of the same split — raw `last` against the `resolveRealtimePrice`
 * mid — is covered where prices can be injected directly, in
 * `tests/fuzz/same-day-pnl-surfaces.fuzz.test.tsx`.
 */
import { expect, test, type Page } from "@playwright/test";

const TODAY = "2026-08-26";
const OPTION_KEY = "META_20260918_580_P";

const PORTFOLIO_MOCK = {
  bankroll: 1_000_000,
  peak_value: 1_000_000,
  last_sync: `${TODAY}T16:32:06Z`,
  total_deployed_pct: 1.2,
  total_deployed_dollars: -12_017,
  remaining_capacity_pct: 98.8,
  position_count: 1,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 1_000_000, daily_pnl: -103, unrealized_pnl: -103,
    realized_pnl: 0, settled_cash: 100_000, maintenance_margin: 0,
    excess_liquidity: 100_000, buying_power: 400_000, dividends: 0,
  },
  positions: [
    {
      id: 42,
      ticker: "META",
      structure: "Short Put $580.0",
      structure_type: "Short Put",
      risk_profile: "undefined",
      expiry: "2026-09-18",
      contracts: 40,
      direction: "SHORT",
      entry_cost: -12_017,
      max_risk: null,
      market_value: -10_920,
      market_price_is_calculated: false,
      // The broker's own daily figure for a position opened this morning. A
      // same-day position must not read it — it is the number the broken card
      // published.
      ib_daily_pnl: -103,
      entry_date: TODAY,
      kelly_optimal: null,
      target: null,
      stop: null,
      legs: [{
        direction: "SHORT",
        contracts: 40,
        type: "Put",
        strike: 580,
        entry_cost: -12_017,
        avg_cost: 300.425,
        // The leg's own mark, which the synced position market_value lags.
        market_price: 3.03,
        market_value: -12_120,
        market_price_is_calculated: false,
      }],
    },
  ],
};

const ORDERS_EMPTY = {
  last_sync: `${TODAY}T16:32:06Z`,
  open_orders: [], executed_orders: [], open_count: 0, executed_count: 0,
};

async function freezeToTradingDay(page: Page) {
  await page.addInitScript((iso) => {
    const fixedNow = new Date(iso).valueOf();
    const RealDate = Date;
    class MockDate extends RealDate {
      constructor(...args: ConstructorParameters<typeof Date>) {
        if (args.length === 0) { super(fixedNow); return; }
        super(...args);
      }
      static now() { return fixedNow; }
    }
    Object.defineProperty(window, "Date", { value: MockDate, configurable: true, writable: true });
  }, `${TODAY}T16:32:06Z`);
}

/** Relay-only mock (Turbopack HMR must pass through, or hydration stalls) that
 *  pushes the wide-spread quote the two market-value walks disagreed on. */
async function installMockWebSocket(page: Page, optionKey: string) {
  await page.addInitScript((key) => {
    const quote = {
      symbol: key, last: 2.73, lastIsCalculated: false,
      bid: 2.73, ask: 3.33, bidSize: 10, askSize: 10,
      volume: 1200, high: 3.6, low: 2.5, open: 3.1, close: 3.2,
      week52High: null, week52Low: null, avgVolume: null,
      delta: -0.51, gamma: null, theta: null, vega: null, impliedVol: 0.31, undPrice: 577.39,
      timestamp: new Date().toISOString(),
    };
    const spot = { ...quote, symbol: "META", last: 577.39, bid: 577.3, ask: 577.45, close: 582.4, undPrice: 577.39 };
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
        window.setTimeout(() => { this.readyState = MockWebSocket.OPEN; this.onopen?.(new Event("open")); }, 0);
        window.setTimeout(() => {
          this.onmessage?.({ data: JSON.stringify({ type: "status", ib_connected: true, ib_issue: null, ib_status_message: null, subscriptions: [] }) } as MessageEvent<string>);
          this.onmessage?.({ data: JSON.stringify({ type: "batch", updates: { [key]: quote, META: spot } }) } as MessageEvent<string>);
        }, 10);
      }
      send(_message: string) {}
      close() { this.readyState = 3; this.onclose?.(new Event("close")); }
    }
    const NativeWebSocket = window.WebSocket;
    const RelayAwareWebSocket = function (url: string | URL, protocols?: string | string[]) {
      return String(url).includes("localhost:8765")
        ? (new MockWebSocket(String(url)) as unknown as WebSocket)
        : new NativeWebSocket(url, protocols);
    } as unknown as typeof WebSocket;
    Object.assign(RelayAwareWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    Object.defineProperty(window, "WebSocket", { configurable: true, writable: true, value: RelayAwareWebSocket });
  }, optionKey);
}

async function setupMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await freezeToTradingDay(page);
  await installMockWebSocket(page, OPTION_KEY);

  const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/portfolio**", (route) => route.fulfill(json(PORTFOLIO_MOCK)));
  await page.route("**/api/orders", (route) => route.fulfill(json(ORDERS_EMPTY)));
  await page.route("**/api/flex-token", (route) => route.fulfill(json({ ok: true, days_until_expiry: 14 })));
  await page.route("**/api/ib-status", (route) => route.fulfill(json({ connected: true })));
  await page.route("**/api/blotter", (route) => route.fulfill(json({ as_of: `${TODAY}T16:32:06Z`, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] })));
  await page.route("**/api/cash-flows**", (route) => route.fulfill(json({ rows: [], summary: {} })));
  await page.route("**/api/prices**", (route) => route.abort());
}

/** The dollar figure a node carries, sign kept, "+" dropped. */
function money(text: string | null): string | null {
  const match = (text ?? "").match(/-?\$[\d,]+/);
  return match ? match[0] : null;
}

test.describe("mobile same-day P&L parity", () => {
  test("Today and the headline P&L are the same number on a position opened today", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    const card = page.getByTestId("mobile-position-META");
    await expect(card).toBeVisible({ timeout: 20_000 });

    const headline = money(await card.getByTestId("mobile-position-pnl").textContent());
    const todayCell = card.locator("text=Today").locator("..");
    const today = money(await todayCell.textContent());

    expect(headline).not.toBeNull();
    expect(today).toBe(headline);

    // The pair that shipped was +$1,097 total against -$103 today. Neither
    // number was invented: each half was right about its own market value.
    expect([headline, today]).not.toEqual(["+$1,097", "-$103"]);
    expect(today).toBe("-$103");
  });
});
