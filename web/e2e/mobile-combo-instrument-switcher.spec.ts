import { expect, test, type Page } from "@playwright/test";

const now = new Date().toISOString();

// Window-relative dates — a hardcoded expiry rots into the past and the
// expired position breaks resolution (2026-08-03: spec froze on the SSR
// desktop shell with "No position" once expiry "2026-03-26" had lapsed).
function daysFromToday(n: number): string {
  const d = new Date(Date.now() + n * 86_400_000);
  return d.toISOString().slice(0, 10);
}

const EXPIRY = daysFromToday(30);
const EXPIRY_KEY = EXPIRY.replace(/-/g, "");

const PORTFOLIO_MOCK = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: now,
  total_deployed_pct: 1.2,
  total_deployed_dollars: 1_200,
  remaining_capacity_pct: 98.8,
  position_count: 2,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [
    {
      id: 12,
      ticker: "IWM",
      structure: "Risk Reversal (P$243.0/C$247.0)",
      structure_type: "Risk Reversal",
      risk_profile: "undefined",
      expiry: EXPIRY,
      contracts: 50,
      direction: "COMBO",
      entry_cost: -579.79,
      max_risk: null,
      market_value: 750,
      market_price_is_calculated: false,
      ib_daily_pnl: 1395.64,
      legs: [
        {
          direction: "LONG",
          contracts: 50,
          type: "Call",
          strike: 247,
          entry_cost: 17285.02,
          avg_cost: 346,
          market_price: 3.63,
          market_value: 18150,
          market_price_is_calculated: false,
        },
        {
          direction: "SHORT",
          contracts: 50,
          type: "Put",
          strike: 243,
          entry_cost: 17864.81,
          avg_cost: 357,
          market_price: 3.88,
          market_value: 19400,
          market_price_is_calculated: false,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: daysFromToday(-7),
    },
    {
      id: 9,
      ticker: "VIX",
      structure: "Ratio Bull Call Spread 500x499 $20/$30",
      structure_type: "Ratio Bull Call Spread",
      risk_profile: "undefined",
      expiry: EXPIRY,
      contracts: 500,
      direction: "DEBIT",
      entry_cost: 40_000,
      max_risk: null,
      market_value: 39_062,
      market_price_is_calculated: true,
      ib_daily_pnl: 0,
      legs: [
        {
          direction: "LONG",
          contracts: 500,
          type: "Call",
          strike: 20,
          entry_cost: 70_000,
          avg_cost: 140,
          market_price: 1.4,
          market_value: 70_000,
          market_price_is_calculated: false,
        },
        {
          direction: "SHORT",
          contracts: 499,
          type: "Call",
          strike: 30,
          entry_cost: 30_000,
          avg_cost: 60.12,
          market_price: 0.62,
          market_value: 30_938,
          market_price_is_calculated: false,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: daysFromToday(-7),
    },
  ],
  account_summary: {
    net_liquidation: 100_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 200_000,
    dividends: 0,
  },
};

const ORDERS_EMPTY = {
  last_sync: now,
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const PRICE_FIXTURES: Record<string, unknown> = {
  VIX: {
    symbol: "VIX",
    last: 18.91,
    lastIsCalculated: false,
    bid: 18.9,
    ask: 18.92,
    bidSize: 10,
    askSize: 10,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 18.5,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: now,
  },
  [`VIX_${EXPIRY_KEY}_20_C`]: {
    symbol: `VIX_${EXPIRY_KEY}_20_C`,
    last: 1.4,
    lastIsCalculated: false,
    bid: 1.39,
    ask: 1.41,
    bidSize: 50,
    askSize: 8_933,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 1.38,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: 18.91,
    timestamp: now,
  },
  [`VIX_${EXPIRY_KEY}_30_C`]: {
    symbol: `VIX_${EXPIRY_KEY}_30_C`,
    last: 0.62,
    lastIsCalculated: false,
    bid: 0.61,
    ask: 0.63,
    bidSize: 999,
    askSize: 4_566,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 0.6,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: 18.91,
    timestamp: now,
  },
  IWM: {
    symbol: "IWM",
    last: 244.65,
    lastIsCalculated: false,
    bid: 244.64,
    ask: 244.66,
    bidSize: 10,
    askSize: 10,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 246,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: now,
  },
  [`IWM_${EXPIRY_KEY}_247_C`]: {
    symbol: `IWM_${EXPIRY_KEY}_247_C`,
    last: 3.63,
    lastIsCalculated: false,
    bid: 3.4,
    ask: 3.46,
    bidSize: 10,
    askSize: 10,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 3.61,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: 244.65,
    timestamp: now,
  },
  [`IWM_${EXPIRY_KEY}_243_P`]: {
    symbol: `IWM_${EXPIRY_KEY}_243_P`,
    last: 3.88,
    lastIsCalculated: false,
    bid: 3.8,
    ask: 3.86,
    bidSize: 10,
    askSize: 10,
    volume: 10,
    high: null,
    low: null,
    open: null,
    close: 3.84,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: 244.65,
    timestamp: now,
  },
};

async function installMockWebSocket(page: Page) {
  await page.addInitScript((priceFixtures) => {
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
          symbol?: string;
          expiry?: string;
          strike?: number;
          right?: "C" | "P";
        };
        if (message.action === "subscribe-depth" && message.symbol && message.expiry && message.strike != null && message.right) {
          const key = `${message.symbol.toUpperCase()}_${message.expiry.replace(/-/g, "")}_${message.strike}_${message.right}`;
          const quote = priceFixtures[key] as { bid?: number; ask?: number } | undefined;
          if (quote?.bid != null && quote.ask != null) {
            this.emit({
              type: "depth",
              symbol: key,
              data: {
                symbol: key,
                kind: "option",
                bid: [{ price: quote.bid, size: 10, marketMaker: null, exchange: "CBOE", nbbo: true }],
                ask: [{ price: quote.ask, size: 10, marketMaker: null, exchange: "BOX", nbbo: true }],
                isSmartDepth: true,
                feed: "OPRA BBO",
                entitled: true,
                timestamp: new Date().toISOString(),
              },
            });
          }
          return;
        }
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
        if (Object.keys(updates).length > 0) this.emit({ type: "batch", updates });
      }

      close() {
        this.readyState = MockWebSocket.CLOSED;
        this.onclose?.({});
      }

      emit(payload: unknown) {
        this.onmessage?.({ data: JSON.stringify(payload) });
      }
    }

    // Only the Radon relay socket is mocked; Turbopack HMR and any other
    // sockets pass through to the real implementation — replacing them too
    // stalls the dev runtime and the page never hydrates past the SSR shell.
    const NativeWebSocket = window.WebSocket;
    const RelayAwareWebSocket = function (url: string | URL, protocols?: string | string[]) {
      return String(url).includes("localhost:8765")
        ? (new MockWebSocket(String(url)) as unknown as WebSocket)
        : new NativeWebSocket(url, protocols);
    } as unknown as typeof WebSocket;
    Object.assign(RelayAwareWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    window.WebSocket = RelayAwareWebSocket;
  }, PRICE_FIXTURES);
}

async function stubApis(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_MOCK) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, level: "LOW" }) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/ib/ws-ticket", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ticket: "test" }) }),
  );
  await page.route("**/api/futures/chain?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        symbol: "VIX",
        exchange: "CFE",
        contracts: [
          {
            conId: 9_001,
            symbol: "VIX",
            localSymbol: "VIXU6",
            exchange: "CFE",
            currency: "USD",
            lastTradeDateOrContractMonth: EXPIRY_KEY,
            multiplier: "1000",
            tradingClass: "VX",
            marketName: "VIX",
            minTick: 0.05,
          },
        ],
        count: 1,
      }),
    }),
  );
  await page.route("**/api/index-options/chain?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        symbol: "VIX",
        exchange: "CBOE",
        tradingClass: "VIX",
        expirations: [EXPIRY_KEY],
        contracts: [],
        count: 0,
      }),
    }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: now, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/ticker/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) }),
  );
}

test("mobile combo position shows STOCK|OPTION switcher", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installMockWebSocket(page);
  await stubApis(page);

  await page.goto("/IWM?posId=12");
  await expect(page.locator(".cockpit--mobile")).toBeVisible({ timeout: 30_000 });

  const switcher = page.getByRole("group", { name: "Instrument view" });
  await expect(switcher).toBeVisible();

  const option = page.getByRole("button", { name: "OPTION" });
  const stock = page.getByRole("button", { name: "STOCK" });
  await expect(option).toHaveAttribute("aria-pressed", "true");
  await expect(stock).toHaveAttribute("aria-pressed", "false");
  await expect(option).toHaveClass(/(^|\s)on(\s|$)/);
  await expect(stock).not.toHaveClass(/(^|\s)on(\s|$)/);
  const legSelector = page.getByRole("group", { name: "Spread and option leg book" });
  await expect(legSelector).toBeVisible();
  const spreadBook = page.getByRole("button", { name: "Implied spread book" });
  const callBook = page.getByRole("button", { name: "$247 Call book" });
  const putBook = page.getByRole("button", { name: "$243 Put book" });
  await expect(spreadBook).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".book-sym")).toContainText("IWM $247C/$243P");
  await expect(page.locator(".book-kind")).toHaveText("IMPLIED SPREAD");
  await expect(page.locator(".book-window")).toContainText("-0.46");
  await expect(page.locator(".book-window")).toContainText("-0.34");
  const screenshotPath = testInfo.outputPath("implied-spread-book-mobile.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await testInfo.attach("implied-spread-book-mobile", {
    path: screenshotPath,
    contentType: "image/png",
  });

  await putBook.click();
  await expect(putBook).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".book-sym")).toContainText("IWM $243P");

  await stock.click();
  await expect(stock).toHaveAttribute("aria-pressed", "true");
  await expect(option).toHaveAttribute("aria-pressed", "false");
  await expect(stock).toHaveClass(/(^|\s)on(\s|$)/);
  await expect(option).not.toHaveClass(/(^|\s)on(\s|$)/);
  await expect(legSelector).toBeHidden();
});

test("STOCK|OPTION labels are vertically centered in their segments", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installMockWebSocket(page);
  await stubApis(page);

  await page.goto("/IWM?posId=12");
  await expect(page.getByRole("group", { name: "Instrument view" })).toBeVisible();

  for (const name of ["STOCK", "OPTION"]) {
    // Measure against the VISIBLE pill (.ckh-instr), not the button: the a11y
    // min-height once forced the button taller than the overflow-hidden pill,
    // so text centered in the button rendered ~6px below the pill's center.
    const offset = await page.getByRole("button", { name }).evaluate((btn) => {
      const pill = btn.closest(".ckh-instr")!;
      const range = document.createRange();
      range.selectNodeContents(btn);
      const text = range.getBoundingClientRect();
      const box = pill.getBoundingClientRect();
      return Math.abs((text.top + text.height / 2) - (box.top + box.height / 2));
    });
    expect(offset, `${name} label off vertical center by ${offset}px`).toBeLessThanOrEqual(1.5);
  }
});

test("VIX 500x499 holdings show a per-contract implied quote", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "desktop cockpit regression");
  await page.setViewportSize({ width: 1440, height: 900 });
  await installMockWebSocket(page);
  await stubApis(page);

  await page.goto("/VIX?posId=9");
  await expect(page.getByTestId("cockpit-host")).toBeVisible({ timeout: 30_000 });

  const implied = page.getByRole("button", { name: "Implied spread book" });
  const long = page.getByRole("button", { name: "$20 Call book" });
  const short = page.getByRole("button", { name: "$30 Call book" });
  const header = page.getByTestId("cockpit-head");
  const book = page.getByTestId("book-window");

  await expect(implied).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("book-kind")).toHaveText("IMPLIED SPREAD");
  await expect(header).toContainText("$0.78");
  await expect(header).toContainText("NET $0.04 / 5.13%");
  await expect(book).toContainText("$0.76");
  await expect(book).toContainText("$0.80");
  await expect(book).not.toContainText("390.62");

  const actTicket = page.getByTestId("act-ticket");
  await expect(actTicket).toBeVisible();
  await expect(actTicket).toContainText("VIX Index");
  await expect(actTicket).toContainText("$18.91");
  await expect(actTicket).not.toContainText("$0.78");

  await long.click();
  await expect(long).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("book-kind")).toHaveText("OPTION");
  await expect(header).toContainText("$1.40");
  await expect(header).toContainText("SPREAD $0.02 / 1.43%");

  await short.click();
  await expect(short).toHaveAttribute("aria-pressed", "true");
  await expect(header).toContainText("$0.62");
  await expect(header).toContainText("SPREAD $0.02 / 3.23%");

  await implied.click();
  await expect(implied).toHaveAttribute("aria-pressed", "true");
  await expect(header).toContainText("$0.78");

  const screenshotPath = testInfo.outputPath("vix-implied-spread-desktop.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await testInfo.attach("vix-implied-spread-desktop", {
    path: screenshotPath,
    contentType: "image/png",
  });
});
