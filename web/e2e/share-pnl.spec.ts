/**
 * E2E: Share P&L card generation + the /orders share popover.
 *
 * SPEC REPAIR 2026-08-08 — drift only, no contract change:
 *  1. Three tests navigated to a hardcoded `http://127.0.0.1:3000/orders`, so
 *     they died on ERR_CONNECTION_REFUSED under PLAYWRIGHT_PORT. All navigation
 *     is now relative and resolves through playwright.config.ts `baseURL`.
 *  2. The popover tests hit an UNSTUBBED /orders, found no `.share-pnl-button`,
 *     and called `test.skip()` — so they had been asserting nothing at all. They
 *     now share the same stub set as the combo-basis test and assert the button
 *     exists instead of skipping when it is missing.
 *  3. Fill timestamps were pinned to 2026-03-17. "Today's Executed Orders" is
 *     day-cut to the ET calendar day (`filterExecutedToEtToday`,
 *     lib/orders/executedToday.ts:39, applied in
 *     components/WorkspaceSections.tsx:3077), so a hardcoded date renders an
 *     empty section forever. Times are now anchored to today in ET while
 *     keeping the original intraday clock, which preserves the minute-bucket
 *     grouping the combo-basis derivation depends on.
 *  4. The historical-trades test only asserted `if (count > 0)` against an
 *     empty blotter — vacuously green. It now stubs a closed blotter trade and
 *     asserts the button unconditionally.
 * Financial expectations are unchanged; the combo-basis numbers are re-derived
 * from the mock in a comment on that test.
 */

import { test, expect } from "@playwright/test";

/** Today's ET calendar day, matching `etCalendarDateString` in lib/orders/executedToday.ts:9. */
const ET_TODAY = new Date().toLocaleDateString("sv", { timeZone: "America/New_York" });

/**
 * A UTC instant on today's ET calendar day. The 14:00-15:59 UTC window used by
 * these fixtures is 10:00-11:59 ET, so the UTC and ET calendar dates agree and
 * the fill survives the ET day-cut year-round.
 */
const etTodayAt = (utcClock: string) => `${ET_TODAY}T${utcClock}+00:00`;

test.describe("Share PnL", () => {
  // --- API route tests ---

  test("API route returns valid PNG for positive P&L", async ({ request }) => {
    const res = await request.get("/api/share/pnl?description=Long+AAOI+2026-04-17+Call+%2445.00&pnl=1234.56&pnlPct=47.5&commission=2.60&fillPrice=12.50&time=2026-03-10");
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("image/png");
    const body = await res.body();
    expect(body.length).toBeGreaterThan(1000);
    // PNG magic bytes
    expect(body[0]).toBe(0x89);
    expect(body[1]).toBe(0x50); // P
    expect(body[2]).toBe(0x4e); // N
    expect(body[3]).toBe(0x47); // G
  });

  test("API route returns valid PNG for negative P&L", async ({ request }) => {
    const res = await request.get("/api/share/pnl?description=Short+TSLA+Put&pnl=-500&pnlPct=-10");
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("image/png");
  });

  test("API route returns 400 when description missing", async ({ request }) => {
    const res = await request.get("/api/share/pnl?pnl=100");
    expect(res.status()).toBe(400);
  });

  test("API route handles pnl-only (no pnlPct)", async ({ request }) => {
    const res = await request.get("/api/share/pnl?description=Long+AAPL&pnl=100");
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("image/png");
  });

  test("API route handles pnlPct-only (no pnl)", async ({ request }) => {
    const res = await request.get("/api/share/pnl?description=Long+AAPL&pnlPct=25.5");
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain("image/png");
  });

  // --- Share popover UI tests ---

  test("clicking share button opens popover with checkboxes", async ({ page }) => {
    await stubOrdersShareApis(page);
    const popover = await openSharePopover(page);
    // Should have two checkboxes
    const checkboxes = popover.locator("input[type='checkbox']");
    await expect(checkboxes).toHaveCount(2);
    // P&L $ should be off, P&L % on by default (SharePnlButton.tsx:72-73)
    await expect(checkboxes.nth(0)).not.toBeChecked();
    await expect(checkboxes.nth(1)).toBeChecked();
  });

  test("popover has Copy & Tweet and Copy buttons", async ({ page }) => {
    await stubOrdersShareApis(page);
    const popover = await openSharePopover(page);
    // Should have a "Copy & Tweet" button and a "Copy" button
    await expect(popover.getByRole("button", { name: "Copy & Tweet" })).toBeVisible();
    await expect(popover.getByRole("button", { name: /^Copy$/ })).toBeVisible();
  });

  test("unchecking P&L $ disables it but keeps % checked", async ({ page }, testInfo) => {
    await stubOrdersShareApis(page);
    const popover = await openSharePopover(page);
    const dollarCheckbox = popover.locator("input[type='checkbox']").nth(0);
    const pctCheckbox = popover.locator("input[type='checkbox']").nth(1);
    // Toggle states to verify % remains enabled
    await dollarCheckbox.uncheck();
    await expect(dollarCheckbox).not.toBeChecked();
    await expect(pctCheckbox).toBeChecked();
    await dollarCheckbox.check();
    await expect(dollarCheckbox).toBeChecked();
    const screenshot = testInfo.outputPath("table-overflow-share-popover.png");
    await page.screenshot({ path: screenshot });
    await testInfo.attach("share-popover-scroll-container", { path: screenshot, contentType: "image/png" });
    await dollarCheckbox.uncheck();
    await expect(dollarCheckbox).not.toBeChecked();
  });

  test("popover closes when clicking outside", async ({ page }) => {
    await stubOrdersShareApis(page);
    const popover = await openSharePopover(page);
    // Dismiss on a mousedown outside the popover container
    // (lib/useDismissablePopover.ts:21-25). The section heading is a safe
    // outside target — body(10,10) lands on the app chrome and can navigate.
    await page.getByRole("heading", { name: /Today's Executed Orders/ }).click();
    await expect(popover).not.toBeVisible({ timeout: 3000 });
  });

  // --- Historical trades ---

  test("share button appears on historical trades for closed trades", async ({ page }) => {
    await stubOrdersShareApis(page);
    // Blotter stub with one CLOSED trade — the executed-orders stub's blotter is
    // empty, and the row only renders a share button when `t.is_closed`
    // (components/WorkspaceSections.tsx:4041). Registered after the base stub so
    // it wins (Playwright matches routes newest-first).
    await page.route("**/api/blotter", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(BLOTTER_WITH_CLOSED_TRADE) }),
    );

    await page.goto("/orders");

    const historical = page.getByTestId("historical-trades-section");
    await expect(historical).toBeVisible({ timeout: 15_000 });
    // The blotter fixture has an open AND a closed AAOI row; under a production
    // `next start` both render, so gate on "at least one AAOI row present"
    // (.first) — strict-mode over 2 matches otherwise fails. The closed-trade
    // share button is still pinned to exactly one below.
    await expect(historical.getByRole("row", { name: /AAOI/ }).first()).toBeVisible({ timeout: 15_000 });
    await expect(historical.locator(".share-pnl-button")).toHaveCount(1);
    await expect(historical.locator(".share-pnl-button")).toBeVisible();
  });
});

const PNG_1X1 = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wn0p1sAAAAASUVORK5CYII=",
  "base64",
);

const PORTFOLIO_MOCK = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [],
  exposure: {},
  violations: [],
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

const ORDERS_MOCK = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [
    {
      execId: "bag-unrelated",
      symbol: "AAOI",
      contract: { conId: 2001, symbol: "AAOI", secType: "BAG", strike: 0, right: "?", expiry: null },
      side: "BOT",
      quantity: 25,
      avgPrice: 0.25,
      commission: 0,
      realizedPNL: null,
      time: etTodayAt("14:01:00"),
      exchange: "SMART",
    },
    {
      execId: "call-unrelated",
      symbol: "AAOI",
      contract: { conId: 1901, symbol: "AAOI", secType: "OPT", strike: 92, right: "C", expiry: "2026-03-27" },
      side: "BOT",
      quantity: 25,
      avgPrice: 5.1,
      commission: -0.61,
      realizedPNL: 0,
      time: etTodayAt("14:01:00"),
      exchange: "SMART",
    },
    {
      execId: "put-unrelated",
      symbol: "AAOI",
      contract: { conId: 1902, symbol: "AAOI", secType: "OPT", strike: 88, right: "P", expiry: "2026-03-27" },
      side: "SLD",
      quantity: 25,
      avgPrice: 5.35,
      commission: -0.64,
      realizedPNL: 0,
      time: etTodayAt("14:01:00"),
      exchange: "SMART",
    },
    {
      execId: "open-call-1",
      symbol: "AAOI",
      contract: { conId: 861001, symbol: "AAOI", secType: "OPT", strike: 90, right: "C", expiry: "2026-03-27" },
      side: "BOT",
      quantity: 12,
      avgPrice: 5.59,
      commission: -8.40,
      realizedPNL: 0,
      time: etTodayAt("14:14:16"),
      exchange: "SMART",
    },
    {
      execId: "open-call-2",
      symbol: "AAOI",
      contract: { conId: 861001, symbol: "AAOI", secType: "OPT", strike: 90, right: "C", expiry: "2026-03-27" },
      side: "BOT",
      quantity: 13,
      avgPrice: 5.59,
      commission: -9.11,
      realizedPNL: 0,
      time: etTodayAt("14:14:16"),
      exchange: "SMART",
    },
    {
      execId: "open-put-1",
      symbol: "AAOI",
      contract: { conId: 858539, symbol: "AAOI", secType: "OPT", strike: 85, right: "P", expiry: "2026-03-27" },
      side: "SLD",
      quantity: 13,
      avgPrice: 6.34,
      commission: -9.12,
      realizedPNL: 0,
      time: etTodayAt("14:12:25"),
      exchange: "SMART",
    },
    {
      execId: "open-put-2",
      symbol: "AAOI",
      contract: { conId: 858539, symbol: "AAOI", secType: "OPT", strike: 85, right: "P", expiry: "2026-03-27" },
      side: "SLD",
      quantity: 12,
      avgPrice: 6.34,
      commission: -8.41,
      realizedPNL: 0,
      time: etTodayAt("14:12:25"),
      exchange: "SMART",
    },
    {
      execId: "close-bag",
      symbol: "AAOI",
      contract: { conId: 2002, symbol: "AAOI", secType: "BAG", strike: 0, right: "?", expiry: null },
      side: "BOT",
      quantity: 25,
      avgPrice: 1.0,
      commission: 0,
      realizedPNL: null,
      time: etTodayAt("15:16:13"),
      exchange: "SMART",
    },
    {
      execId: "close-call",
      symbol: "AAOI",
      contract: { conId: 861001, symbol: "AAOI", secType: "OPT", strike: 90, right: "C", expiry: "2026-03-27" },
      side: "SLD",
      quantity: 25,
      avgPrice: 5.33,
      commission: -1.03,
      realizedPNL: 2200,
      time: etTodayAt("15:16:13"),
      exchange: "SMART",
    },
    {
      execId: "close-put",
      symbol: "AAOI",
      contract: { conId: 858539, symbol: "AAOI", secType: "OPT", strike: 85, right: "P", expiry: "2026-03-27" },
      side: "BOT",
      quantity: 25,
      avgPrice: 7.83,
      commission: -1.03,
      realizedPNL: 2137.9,
      time: etTodayAt("15:16:13"),
      exchange: "SMART",
    },
  ],
  open_count: 0,
  executed_count: 9,
};

/** One CLOSED blotter trade so the Historical Trades row renders a share button. */
const BLOTTER_WITH_CLOSED_TRADE = {
  as_of: ET_TODAY,
  summary: { closed_trades: 1, open_trades: 0, total_commissions: -2.06, realized_pnl: 4337.9 },
  closed_trades: [
    {
      symbol: "AAOI",
      contract_desc: "AAOI 2026-03-27 90 C",
      sec_type: "OPT",
      is_closed: true,
      net_quantity: 0,
      total_quantity: 25,
      total_commission: -2.06,
      realized_pnl: 4337.9,
      realized_cost_basis: 1875,
      cost_basis: 1875,
      proceeds: 6212.9,
      total_cash_flow: 4337.9,
      executions: [
        { exec_id: "h-open", time: etTodayAt("14:14:16"), side: "BUY", quantity: 25, price: 5.59, commission: -1.03, notional_value: 13975, net_cash_flow: -13975 },
        { exec_id: "h-close", time: etTodayAt("15:16:13"), side: "SELL", quantity: 25, price: 5.33, commission: -1.03, notional_value: 13325, net_cash_flow: 13325 },
      ],
    },
  ],
  open_trades: [],
};

const SPCX_BULL_CALL_PORTFOLIO = {
  ...PORTFOLIO_MOCK,
  position_count: 1,
  defined_risk_count: 1,
  positions: [
    {
      id: 42,
      ticker: "SPCX",
      structure: "Bull Call Spread $155/$170",
      structure_type: "defined",
      risk_profile: "defined",
      expiry: "2026-10-16",
      contracts: 10,
      direction: "LONG",
      entry_cost: 3980,
      max_risk: 3980,
      market_value: 5080,
      legs: [
        {
          direction: "LONG",
          contracts: 10,
          type: "Call",
          strike: 155,
          entry_cost: 4980,
          avg_cost: 498,
          market_price: 6.1,
          market_value: 6100,
        },
        {
          direction: "SHORT",
          contracts: 10,
          type: "Call",
          strike: 170,
          entry_cost: -1000,
          avg_cost: 100,
          market_price: 1.02,
          market_value: -1020,
        },
      ],
      kelly_optimal: 0.025,
      target: null,
      stop: null,
      entry_date: "2026-09-04",
    },
  ],
};

const SPCX_BULL_CALL_ORDERS = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [
    {
      execId: "spcx-close-long",
      symbol: "SPCX",
      contract: { conId: 155001, symbol: "SPCX", secType: "OPT", strike: 155, right: "C", expiry: "2026-10-16" },
      side: "SLD",
      quantity: 10,
      avgPrice: 6.1,
      commission: -2.5,
      realizedPNL: 800,
      time: etTodayAt("18:29:00"),
      exchange: "SMART",
    },
    {
      execId: "spcx-close-short",
      symbol: "SPCX",
      contract: { conId: 170001, symbol: "SPCX", secType: "OPT", strike: 170, right: "C", expiry: "2026-10-16" },
      side: "BOT",
      quantity: 10,
      avgPrice: 1.02,
      commission: -2.5,
      realizedPNL: 300,
      time: etTodayAt("18:29:00"),
      exchange: "SMART",
    },
    {
      execId: "spcx-close-bag",
      symbol: "SPCX",
      contract: { conId: 155170, symbol: "SPCX", secType: "BAG", strike: 0, right: "?", expiry: null },
      side: "SLD",
      quantity: 10,
      avgPrice: 5.08,
      commission: 0,
      realizedPNL: null,
      time: etTodayAt("18:29:00"),
      exchange: "SMART",
    },
  ],
  open_count: 0,
  executed_count: 3,
};

async function stubOrdersShareApis(
  page: import("@playwright/test").Page,
  portfolio: typeof PORTFOLIO_MOCK = PORTFOLIO_MOCK,
  orders: typeof ORDERS_MOCK = ORDERS_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { write: async () => undefined },
    });
    // @ts-expect-error test shim
    window.ClipboardItem = class ClipboardItem {
      items: Record<string, Blob>;

      constructor(items: Record<string, Blob>) {
        this.items = items;
      }
    };
  });

  await page.route("**/api/portfolio**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(portfolio) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(orders) }),
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
  await page.route("**/api/prices", (route) => route.abort());
}

/** The share button on the closing group inside Today's Executed Orders. */
function executedShareButton(page: import("@playwright/test").Page) {
  return page.locator("#orders-executed .share-pnl-button").first();
}

/** Open /orders (stubs must already be installed) and expand the share popover. */
async function openSharePopover(page: import("@playwright/test").Page) {
  await page.goto("/orders");
  const shareBtn = executedShareButton(page);
  await expect(shareBtn).toBeVisible({ timeout: 15_000 });
  await shareBtn.click();
  const popover = page.locator(".share-pnl-popover");
  await expect(popover).toBeVisible({ timeout: 5_000 });
  return popover;
}

test.describe("Share PnL signed combo basis", () => {
  test("uses the matching opening legs for signed risk-reversal entry basis", async ({ page }) => {
    await stubOrdersShareApis(page);

    let shareRequestUrl: string | null = null;
    await page.route("**/api/share/pnl?*", (route) => {
      shareRequestUrl = route.request().url();
      return route.fulfill({ status: 200, contentType: "image/png", body: PNG_1X1 });
    });

    await page.goto("/orders");

    const shareButton = executedShareButton(page);
    await expect(shareButton).toBeVisible({ timeout: 15_000 });

    // Copy drift: `buildExecutedGroupDescription` hoists a SHARED leg expiry to
    // structure level (lib/openOrderCombos.ts:272-274 + :324-325), so the label
    // is "Risk Reversal 3/27 (…)". `formatExpiryShort` (:77-83) is a pure M/D of
    // the contract expiry, so "3/27" is fixture-deterministic, not date-relative.
    // The table cell then strips the leading "Closed AAOI "
    // (components/WorkspaceSections.tsx:3660).
    const closedRow = shareButton.locator("xpath=ancestor::tr[1]");
    await expect(closedRow).toContainText("Risk Reversal 3/27 (Short $85 Put / Long $90 Call)");
    await expect(closedRow).toContainText("$1.00");
    await shareButton.click();
    const popover = page.locator(".share-pnl-popover");
    await expect(popover).toBeVisible();
    await popover.getByRole("button", { name: /^Copy$/ }).click();

    await expect.poll(() => shareRequestUrl).not.toBeNull();

    // Derivation from ORDERS_MOCK via `resolveOpeningLegBasis`
    // (components/WorkspaceSections.tsx:250-321) — matched by contract, cash
    // sign SLD +/BOT −, per combo unit:
    //   opening calls  25 × BOT @ 5.59 → −139.75
    //   opening puts   25 × SLD @ 6.34 → +158.50
    //   netCash = +18.75; comboUnits = BAG quantity = 25
    //   entryPrice = −(18.75 / 25)         = −0.75   (net CREDIT of $0.75)
    //   entryNotional = |18.75| × 100      = $1,875
    //   exitPrice = closing BAG avgPrice   =  1.00
    //   totalPnL = 2200 + 2137.90          = $4,337.90
    //   pnlPct = 4337.90 / 1875 × 100      = 231.3547%
    const params = new URL(shareRequestUrl ?? "http://localhost").searchParams;
    expect(params.get("entryPrice")).toBe("-0.75");
    expect(params.get("exitPrice")).toBe("1");
    expect(Number(params.get("pnlPct"))).toBeCloseTo(231.3547, 3);
  });

  test("portfolio-fallback bull call share entry is the debit paid, not a negative cash-flow", async ({ page }) => {
    await stubOrdersShareApis(page, SPCX_BULL_CALL_PORTFOLIO, SPCX_BULL_CALL_ORDERS);

    let shareRequestUrl: string | null = null;
    await page.route("**/api/share/pnl?*", (route) => {
      shareRequestUrl = route.request().url();
      return route.fulfill({ status: 200, contentType: "image/png", body: PNG_1X1 });
    });

    await page.goto("/orders");
    const shareButton = executedShareButton(page);
    await expect(shareButton).toBeVisible({ timeout: 15_000 });
    await shareButton.click();
    const popover = page.locator(".share-pnl-popover");
    await expect(popover).toBeVisible();
    await popover.getByRole("button", { name: /^Copy$/ }).click();
    await expect.poll(() => shareRequestUrl).not.toBeNull();

    const params = new URL(shareRequestUrl ?? "http://localhost").searchParams;
    expect(Number(params.get("entryPrice"))).toBeCloseTo(3.98, 2);
    expect(Number(params.get("entryPrice"))).toBeGreaterThan(0);
    expect(params.get("exitPrice")).toBe("5.08");
  });
});

test.describe("Share PnL Instagram Story", () => {
  test("story route renders a 1080x1920 PNG", async ({ request }) => {
    const response = await request.get("/api/share/pnl", {
      params: { description: "Closed AAOI (Long $115 Call)", pnlPct: "44.08", format: "story" },
    });
    expect(response.ok()).toBe(true);
    expect(response.headers()["content-type"]).toBe("image/png");
    const png = await response.body();
    expect(png.readUInt32BE(16)).toBe(1080);
    expect(png.readUInt32BE(20)).toBe(1920);
  });

  test("shows the story image, then downloads it when the browser cannot share files", async ({ page }, testInfo) => {
    await page.addInitScript(() => {
      // Desktop Chrome: no file share target, so the image stays on screen.
      Object.defineProperty(navigator, "share", { configurable: true, value: undefined });
      Object.defineProperty(navigator, "canShare", { configurable: true, value: undefined });
    });
    await stubOrdersShareApis(page);
    let storyUrl: string | null = null;
    await page.route("**/api/share/pnl?*", (route) => {
      storyUrl = route.request().url();
      return route.fulfill({ status: 200, contentType: "image/png", body: PNG_1X1 });
    });

    const popover = await openSharePopover(page);
    const story = popover.getByRole("button", { name: "Instagram Story" });
    await expect(story).toBeVisible();
    await story.click();
    const preview = popover.getByRole("img", { name: "Instagram story preview" });
    await expect(preview).toBeVisible();
    await expect(preview).toHaveAttribute("src", /^blob:/);
    await expect(popover).toBeVisible();
    await expect(popover.getByRole("button", { name: "Download Story" })).toBeVisible();
    const shot = testInfo.outputPath("share-popover-story.png");
    await popover.screenshot({ path: shot });
    await testInfo.attach("share-popover-story", { path: shot, contentType: "image/png" });

    const download = page.waitForEvent("download");
    await popover.getByRole("button", { name: "Download Story" }).click();
    expect((await download).suggestedFilename()).toBe("radon-pnl-story.png");
    await expect(popover).toBeVisible();
    const url = new URL(storyUrl ?? "http://localhost");
    expect(url.pathname).toBe("/api/share/pnl");
    expect(url.searchParams.get("format")).toBe("story");
  });

  test("hands the story PNG to the native share sheet when files are shareable", async ({ page }) => {
    await page.addInitScript(() => {
      const w = window as unknown as { __shared: { name: string; type: string; size: number }[] };
      w.__shared = [];
      Object.defineProperty(navigator, "canShare", { configurable: true, value: () => true });
      Object.defineProperty(navigator, "share", {
        configurable: true,
        value: async (data: ShareData) => {
          for (const f of data.files ?? []) w.__shared.push({ name: f.name, type: f.type, size: f.size });
        },
      });
    });
    await stubOrdersShareApis(page);
    await page.route("**/api/share/pnl?*", (route) =>
      route.fulfill({ status: 200, contentType: "image/png", body: PNG_1X1 }),
    );

    const popover = await openSharePopover(page);
    await popover.getByRole("button", { name: "Instagram Story" }).click();
    await expect
      .poll(() => page.evaluate(() => (window as unknown as { __shared: unknown[] }).__shared))
      .toEqual([{ name: "radon-pnl-story.png", type: "image/png", size: PNG_1X1.length }]);
    await expect(popover).not.toBeVisible();
  });
});

// REL-108 / R-317: the common report-share owner must recover from transport faults.
import { stubReliabilityCta } from "./fixtures/reliability-cta";

test.describe("report share reliability", () => {
  test("HTML failure reports the service outage and releases sharing", async ({page}, testInfo) => {
    await stubReliabilityCta(page);
    const requests: {url: string; method: string; body: string | null}[] = [];
    await page.route("**/api/menthorq/cta/share", route => {
      requests.push({url: new URL(route.request().url()).pathname,
        method: route.request().method(), body: route.request().postData()});
      return route.fulfill({status: 502, contentType: "text/html", body: "<html>upstream unavailable</html>"});
    });
    await page.goto("/cta");
    const share = page.getByRole("button", {name: "Share to X", exact: true});
    await share.click();
    await expect(page.getByRole("alert").filter({hasText: "This service is temporarily unavailable"})).toBeVisible();
    await expect(share).toBeEnabled();
    expect(requests).toEqual([{url: "/api/menthorq/cta/share", method: "POST", body: null}]);
    await page.screenshot({path: testInfo.outputPath("report-share-outage.png")});
  });

  for (const phase of ["generation", "content"]) {
    test(`hung ${phase} is bounded and a recovered request succeeds`, async ({page}, testInfo) => {
      await stubReliabilityCta(page);
      await page.clock.install();
      const requests: string[] = [];
      let recover = false;
      await page.route("**/api/menthorq/cta/share", async route => {
        requests.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
        if (phase === "generation" && !recover) return;
        await route.fulfill({json: {preview_path: "/reports/mock-preview.html"}});
      });
      await page.route("**/api/menthorq/cta/share/content?*", async route => {
        const url = new URL(route.request().url());
        requests.push(`${route.request().method()} ${url.pathname}${url.search}`);
        if (phase === "content" && !recover) return;
        await route.fulfill({contentType: "text/html", body: "<html><body>Mock report preview</body></html>"});
      });
      await page.goto("/cta");
      const share = page.getByRole("button", {name: "Share to X", exact: true});
      await share.click();
      await expect(page.getByRole("button", {name: "Generating…"})).toBeDisabled();
      if (phase === "content") await expect.poll(() => requests.length).toBe(2);
      await page.clock.fastForward(30_001);
      await expect(share).toBeEnabled();
      await expect(page.getByRole("alert").filter({hasText: "share request took too long"})).toBeVisible();
      const initial = ["POST /api/menthorq/cta/share"];
      const content = "GET /api/menthorq/cta/share/content?path=%2Freports%2Fmock-preview.html";
      if (phase === "content") initial.push(content);
      expect(requests).toEqual(initial);
      recover = true;
      await share.click();
      await expect(page.getByRole("dialog", {name: "CTA Share Preview"})).toBeVisible();
      expect(requests).toEqual([...initial, "POST /api/menthorq/cta/share", content]);
      await expect(share).toBeEnabled();
      await page.screenshot({path: testInfo.outputPath(`report-share-${phase}-recovered.png`)});
    });
  }
});

/** REL-108 / R-318: the bond callout describes the measured band. */
test.describe("CTA bond percentile reliability", () => {
  for (const [label, percentiles] of [["boundary", [0, 10]], ["interior", [8, 9]]] as const) {
    test(`${label} values describe the band instead of inventing an exact percentile`, async ({page}, testInfo) => {
      await stubReliabilityCta(page, [...percentiles]);
      await page.goto("/cta");
      await expect(page.getByText("2 bond contracts at or below 10th pctile, full duration short.", {exact: false})).toBeVisible();
      await expect(page.getByText(/bond contracts at 0th pctile/)).toHaveCount(0);
      await page.getByText("2 bond contracts at or below 10th pctile, full duration short.", {exact: false}).scrollIntoViewIfNeeded();
      await page.screenshot({path: testInfo.outputPath(`cta-bond-${label}.png`)});
    });
  }
  test("values above the band do not count as extreme shorts", async ({page}) => {
    await stubReliabilityCta(page, [11, 9]);
    await page.goto("/cta");
    await expect(page.getByRole("cell", {name: "Treasury 1", exact: true})).toBeVisible();
    await expect(page.getByText(/bond contracts at/)).toHaveCount(0);
  });
});

/** REL-108 / R-316: render the real shared export theme with the OG engine. */
import { createElement } from "react";
import { ImageResponse } from "next/og.js";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import { loadFonts } from "../lib/og-fonts";
// Playwright runs ESM directly; compile the actual theme to CJS so its JSON
// import is resolved just as Next resolves it, without an ESM JSON assertion.
const ogPath = resolve(process.cwd(), "lib/og-theme.ts");
const ogModule = {exports: {} as typeof import("../lib/og-theme")};
runInNewContext(ts.transpileModule(readFileSync(ogPath, "utf8"), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, esModuleInterop: true},
}).outputText, {module: ogModule, exports: ogModule.exports, require: createRequire(ogPath)});
const { OG } = ogModule.exports;
const brandTokens = JSON.parse(readFileSync(resolve(process.cwd(), "../brand/radon-design-tokens.json"), "utf8"));

test("OG reliability plate matches accessible export-kit tokens", async ({page}, testInfo) => {
  const kit = brandTokens.color.dark;
  expect(OG.panelRaised).toBe(kit.bg.panelRaised);
  expect(OG.border).toBe(kit.line.grid);
  expect(OG.faint).toBe(kit.text.muted);
  const response = new ImageResponse(createElement("div", {style: {
    display: "flex", width: "100%", height: "100%", background: OG.bg,
    color: OG.text, fontFamily: "IBM Plex Mono", padding: 24,
  }}, createElement("div", {style: {
    display: "flex", flexDirection: "column", width: "100%", background: OG.panel,
    border: `1px solid ${OG.border}`, padding: 20,
  }}, createElement("div", {style: {fontSize: 24}}, "RADON / EXPORT RELIABILITY"),
  createElement("div", {style: {fontSize: 18, color: OG.faint, marginTop: 16}}, "Supporting measurement text"),
  createElement("div", {style: {display: "flex", background: OG.panelRaised, color: OG.muted, padding: 12, marginTop: 16}}, "Raised instrument panel"))),
  {width: 760, height: 240, fonts: await loadFonts()});
  const png = Buffer.from(await response.arrayBuffer());
  await page.route("**/reliability-og.png", route => route.fulfill({contentType: "image/png", body: png}));
  await page.goto("/reliability-og.png");
  await expect(page.locator("img")).toBeVisible();
  expect(await page.locator("img").evaluate((image: HTMLImageElement) => [image.naturalWidth, image.naturalHeight])).toEqual([760, 240]);
  await page.screenshot({path: testInfo.outputPath("accessible-og-plate.png")});
});
