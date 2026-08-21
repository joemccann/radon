import { expect, test } from "@playwright/test";

const PORTFOLIO = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 1,
  defined_risk_count: 0,
  undefined_risk_count: 1,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [
    {
      id: 1,
      ticker: "AAOI",
      structure: "Risk Reversal",
      structure_type: "Risk Reversal",
      risk_profile: "Undefined",
      expiry: "2026-03-27",
      contracts: 50,
      direction: "COMBO",
      entry_cost: 0,
      max_risk: null,
      market_value: 0,
      legs: [
        { direction: "SHORT", contracts: 50, type: "Put", strike: 90, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
        { direction: "LONG", contracts: 50, type: "Call", strike: 98, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
      ],
      ib_daily_pnl: null,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-18",
    },
  ],
  account_summary: {
    net_liquidation: 100_000,
    daily_pnl: null,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 200_000,
    dividends: 0,
  },
};

const ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 77,
      permId: 653611587,
      symbol: "AAOI Spread",
      contract: {
        conId: 28812380,
        symbol: "AAOI",
        secType: "BAG",
        strike: 0,
        right: "?",
        expiry: null,
        comboLegs: [
          { conId: 859556931, ratio: 1, action: "SELL", symbol: "AAOI", strike: 90, right: "P", expiry: "2026-03-27" },
          { conId: 861002104, ratio: 1, action: "BUY", symbol: "AAOI", strike: 98, right: "C", expiry: "2026-03-27" },
        ],
      },
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 50,
      limitPrice: 0.6,
      auxPrice: 0,
      status: "Submitted",
      filled: 0,
      remaining: 50,
      avgFillPrice: 0,
      tif: "DAY",
    },
  ],
  executed_orders: [],
  open_count: 1,
  executed_count: 0,
};

const MSFT_PORTFOLIO = {
  ...PORTFOLIO,
  positions: [
    {
      id: 2,
      ticker: "MSFT",
      structure: "Risk Reversal",
      structure_type: "Risk Reversal",
      risk_profile: "Undefined",
      expiry: "2026-07-17",
      contracts: 25,
      direction: "COMBO",
      entry_cost: 0,
      max_risk: null,
      market_value: 0,
      legs: [
        { direction: "SHORT", contracts: 25, type: "Put", strike: 350, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
        { direction: "LONG", contracts: 25, type: "Call", strike: 375, entry_cost: 0, avg_cost: 0, market_price: 0, market_value: 0 },
      ],
      ib_daily_pnl: null,
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-06-25",
    },
  ],
};

const MSFT_ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [
    {
      orderId: 77,
      permId: 653611587,
      symbol: "MSFT Spread",
      contract: {
        conId: 28812380,
        symbol: "MSFT",
        secType: "BAG",
        strike: 0,
        right: "?",
        expiry: null,
        comboLegs: [
          { conId: 859556931, ratio: 1, action: "SELL", symbol: "MSFT", strike: 350, right: "P", expiry: "2026-07-17" },
          { conId: 861002104, ratio: 1, action: "BUY", symbol: "MSFT", strike: 375, right: "C", expiry: "2026-07-17" },
        ],
      },
      action: "BUY",
      orderType: "LMT",
      totalQuantity: 25,
      limitPrice: -3.65,
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

const MSFT_PRICE_FIXTURES = {
  MSFT: {
    symbol: "MSFT",
    last: 355.54,
    lastIsCalculated: false,
    bid: 355.5,
    ask: 355.6,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: 365.45,
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
  MSFT_20260717_350_P: {
    symbol: "MSFT_20260717_350_P",
    last: 6.75,
    lastIsCalculated: false,
    bid: 6.6,
    ask: 6.9,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.28,
    undPrice: 355.54,
    timestamp: new Date().toISOString(),
  },
  MSFT_20260717_375_C: {
    symbol: "MSFT_20260717_375_C",
    last: 3.2,
    lastIsCalculated: false,
    bid: 3.05,
    ask: 3.35,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.28,
    undPrice: 355.54,
    timestamp: new Date().toISOString(),
  },
};

async function installMockWebSocket(
  page: import("@playwright/test").Page,
  priceFixtures: Record<string, unknown>,
) {
  await page.addInitScript((fixtures) => {
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
          const key = String(symbol).toUpperCase();
          if (fixtures[key]) updates[key] = fixtures[key];
        }
        for (const contract of message.contracts ?? []) {
          const expiry = String(contract.expiry).replace(/-/g, "");
          const key = `${String(contract.symbol).toUpperCase()}_${expiry}_${Number(contract.strike)}_${contract.right}`;
          if (fixtures[key]) updates[key] = fixtures[key];
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
  }, priceFixtures);
}

async function stubApis(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        as_of: new Date().toISOString(),
        summary: { closed_trades: 0, open_trades: 0, total_commissions: 0, realized_pnl: 0 },
        closed_trades: [],
        open_trades: [],
      }),
    }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/prices", (route) => route.abort());
}

test.describe("Combo order modify flow", () => {
  test("submits combo replacement payload with edited quantity and legs", async ({ page }) => {
    await stubApis(page);

    let modifyBody: Record<string, unknown> | null = null;
    await page.route("**/api/orders/modify", async (route) => {
      modifyBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", message: "Replacement placed", orders: ORDERS }),
      });
    });

    await page.goto("/orders");

    const row = page.locator("tbody tr").filter({ hasText: "AAOI" }).first();
    await expect(row).toBeVisible({ timeout: 10_000 });
    await row.getByRole("button", { name: "MODIFY" }).click();

    const modal = page.locator(".modify-dialog");
    await expect(modal).toBeVisible();
    await expect(modal.getByText("Edit Legs")).toBeVisible();

    const modalContent = page.locator(".modal-content");
    const box = await modalContent.boundingBox();
    expect(box?.width ?? 0).toBeGreaterThan(720);

    const overflow = await modal.locator(".modify-secondary-panel").evaluate((el) => ({
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
    }));
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1);

    await modal.locator("#modify-price-input").focus();
    const focusedPriceLayout = await modal.locator("#modify-price-input").evaluate((input) => {
      const row = input.closest(".modify-price-input-row");
      const field = input.closest(".modify-field");
      const primaryPanel = input.closest(".modify-primary-panel");
      if (!row || !field || !primaryPanel) {
        throw new Error("Missing modify order price field layout");
      }
      const rowBox = row.getBoundingClientRect();
      const fieldBox = field.getBoundingClientRect();
      const panel = primaryPanel as HTMLElement;
      return {
        rowLeft: rowBox.left,
        rowRight: rowBox.right,
        fieldLeft: fieldBox.left,
        fieldRight: fieldBox.right,
        inputBoxShadow: getComputedStyle(input).boxShadow,
        rowFocusWithin: row.matches(":focus-within"),
        panelScrollWidth: panel.scrollWidth,
        panelClientWidth: panel.clientWidth,
      };
    });
    expect(focusedPriceLayout.rowLeft).toBeGreaterThanOrEqual(focusedPriceLayout.fieldLeft - 1);
    expect(focusedPriceLayout.rowRight).toBeLessThanOrEqual(focusedPriceLayout.fieldRight + 1);
    expect(focusedPriceLayout.panelScrollWidth).toBeLessThanOrEqual(focusedPriceLayout.panelClientWidth + 1);
    expect(focusedPriceLayout.inputBoxShadow).toBe("none");
    expect(focusedPriceLayout.rowFocusWithin).toBe(true);

    await expect(modal.locator("#modify-quantity-input")).toHaveValue("50");
    await expect(modal.locator("#modify-leg-0-strike")).toHaveValue("90");
    await expect(modal.locator("#modify-leg-1-strike")).toHaveValue("98");
    await expect(modal.locator("#modify-leg-0-action")).toBeVisible();
    await expect(modal.locator("#modify-leg-0-expiry")).toBeVisible();
    await expect(modal.locator("#modify-leg-1-ratio")).toBeVisible();

    await modal.locator("#modify-quantity-input").fill("75");
    await modal.locator("#modify-price-input").fill("0.75");
    await modal.locator("#modify-leg-1-strike").fill("100");
    await modal.getByRole("button", { name: /modify order/i }).click();

    await expect.poll(() => modifyBody).not.toBeNull();
    expect(modifyBody).toMatchObject({
      orderId: 77,
      permId: 653611587,
      replaceOrder: {
        type: "combo",
        symbol: "AAOI",
        action: "SELL",
        quantity: 75,
        limitPrice: 0.75,
        tif: "DAY",
        legs: [
          { action: "SELL", right: "P", strike: 90, expiry: "20260327", ratio: 1 },
          { action: "BUY", right: "C", strike: 100, expiry: "20260327", ratio: 1 },
        ],
      },
    });
  });

  test("shows close credit and realized P&L when modifying a SELL combo that flattens a held reversal", async ({ page }) => {
    const portfolio = {
      ...PORTFOLIO,
      positions: [
        {
          ...PORTFOLIO.positions[0],
          entry_cost: 25_000,
          legs: [
            { direction: "SHORT", contracts: 50, type: "Put", strike: 90, expiry: "2026-03-27", entry_cost: -30_000, avg_cost: 600, market_price: 0, market_value: 0 },
            { direction: "LONG", contracts: 50, type: "Call", strike: 98, expiry: "2026-03-27", entry_cost: 55_000, avg_cost: 1_100, market_price: 0, market_value: 0 },
          ],
        },
      ],
    };
    await stubApis(page);
    await page.route("**/api/portfolio", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(portfolio) }),
    );

    await page.goto("/orders");
    const row = page.locator("tbody tr").filter({ hasText: "AAOI" }).first();
    await expect(row).toBeVisible({ timeout: 10_000 });
    await row.getByRole("button", { name: "MODIFY" }).click();

    const modal = page.locator(".modify-dialog");
    await expect(modal).toBeVisible();
    await modal.locator("#modify-price-input").fill("8");

    const summary = modal.locator(".order-confirm-summary");
    await expect(summary).toContainText("Close Credit:");
    await expect(summary).toContainText("$40,000");
    await expect(summary).toContainText("Est. Realized P&L:");
    await expect(summary).toContainText("$15,000");
    await expect(summary).not.toContainText("Max Gain:");
    await expect(summary).not.toContainText("Max Loss:");
    await modal.screenshot({ path: "test-results/modify-combo-close-pnl.png" });
  });

  test("shows signed negative risk reversal prices and submits a negative replacement limit", async ({ page }) => {
    await installMockWebSocket(page, MSFT_PRICE_FIXTURES);
    await page.unrouteAll({ behavior: "ignoreErrors" });

    await page.route("**/api/portfolio", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MSFT_PORTFOLIO) }),
    );
    await page.route("**/api/orders", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MSFT_ORDERS) }),
    );
    await page.route("**/api/blotter", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          as_of: new Date().toISOString(),
          summary: { closed_trades: 0, open_trades: 0, total_commissions: 0, realized_pnl: 0 },
          closed_trades: [],
          open_trades: [],
        }),
      }),
    );
    await page.route("**/api/ib-status", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
    );
    await page.route("**/api/regime", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
    );
    await page.route("**/api/prices", (route) => route.abort());

    let modifyBody: Record<string, unknown> | null = null;
    await page.route("**/api/orders/modify", async (route) => {
      modifyBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", message: "Replacement placed", orders: MSFT_ORDERS }),
      });
    });

    await page.goto("/orders");

    const row = page.locator("tbody tr").filter({ hasText: "MSFT" }).first();
    await expect(row).toBeVisible({ timeout: 10_000 });
    await row.getByRole("button", { name: "MODIFY" }).click();

    const modal = page.locator(".modify-dialog");
    await expect(modal).toBeVisible();
    await expect(modal.getByRole("button", { name: /BID -3\.65/i })).toBeVisible();
    await expect(modal.getByRole("button", { name: /MID -3\.45/i })).toBeVisible();
    await expect(modal.getByRole("button", { name: /ASK -3\.25/i })).toBeVisible();
    await expect(modal.getByRole("button", { name: /IMPLIED -/i })).toBeVisible();

    await expect.poll(() => modal.locator("#modify-price-input").getAttribute("min")).toBeNull();
    await modal.locator("#modify-price-input").fill("-3.40");
    await expect(modal.getByRole("button", { name: /modify order/i })).toBeEnabled();
    await modal.getByRole("button", { name: /modify order/i }).click();

    await expect.poll(() => modifyBody).not.toBeNull();
    expect(modifyBody).toMatchObject({
      orderId: 77,
      permId: 653611587,
      replaceOrder: {
        type: "combo",
        symbol: "MSFT",
        action: "BUY",
        quantity: 25,
        limitPrice: -3.4,
        tif: "DAY",
        legs: [
          { action: "SELL", right: "P", strike: 350, expiry: "20260717", ratio: 1 },
          { action: "BUY", right: "C", strike: 375, expiry: "20260717", ratio: 1 },
        ],
      },
    });
  });
});
