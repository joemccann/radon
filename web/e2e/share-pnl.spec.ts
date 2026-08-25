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

  test("unchecking P&L $ disables it but keeps % checked", async ({ page }) => {
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

async function stubOrdersShareApis(page: import("@playwright/test").Page) {
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
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_MOCK) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_MOCK) }),
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
});
