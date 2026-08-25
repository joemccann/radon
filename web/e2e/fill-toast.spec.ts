/**
 * E2E: Persistent fill toast (WorkspaceShell fill watcher)
 *
 * Verifies the per-execution toast wired into WorkspaceShell via
 * `useFillToasts` (web/lib/useFillToasts.ts):
 *  - First orders payload primes the baseline: today's existing fills do NOT
 *    toast on page load (no storm)
 *  - A new execution row arriving on a subsequent poll raises exactly one
 *    success toast with the terse FILLED copy
 *  - The toast is persistent (duration 0 — survives past the 5s default)
 *  - × close button dismisses it
 *
 * The orders poll interval is 30s, so the new-fill test waits out one poll
 * tick. Runs on /orders where the poll is always active regardless of
 * market hours.
 */

import { test, expect, type Page } from "@playwright/test";

type FillFixture = {
  execId: string;
  symbol: string;
  secType: string;
  strike: number | null;
  right: string | null;
  side: string;
  quantity: number;
  avgPrice: number;
};

function executedOrder(f: FillFixture) {
  return {
    execId: f.execId,
    symbol: f.symbol,
    contract: {
      conId: 1,
      symbol: f.symbol,
      secType: f.secType,
      strike: f.strike,
      right: f.right,
      expiry: f.secType === "OPT" ? "20260918" : null,
    },
    side: f.side,
    quantity: f.quantity,
    avgPrice: f.avgPrice,
    commission: -1.1,
    realizedPNL: null,
    time: new Date().toISOString(),
    exchange: "SMART",
  };
}

const BASELINE_FILLS = [
  executedOrder({
    execId: "0000e0d5.101.01",
    symbol: "AAPL",
    secType: "STK",
    strike: null,
    right: null,
    side: "BOT",
    quantity: 100,
    avgPrice: 212.3,
  }),
  executedOrder({
    execId: "0000e0d5.102.01",
    symbol: "MU",
    secType: "OPT",
    strike: 120,
    right: "C",
    side: "BOT",
    quantity: 5,
    avgPrice: 2.1,
  }),
];

const NEW_FILL = executedOrder({
  execId: "0000e0d5.999.01",
  symbol: "EWY",
  secType: "OPT",
  strike: 175,
  right: "C",
  side: "SLD",
  quantity: 25,
  avgPrice: 4.55,
});

function ordersPayload(executed: ReturnType<typeof executedOrder>[]) {
  return {
    last_sync: new Date().toISOString(),
    open_orders: [],
    executed_orders: executed,
    open_count: 0,
    executed_count: executed.length,
  };
}

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
  positions: [],
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 1_000_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 0,
    maintenance_margin: 100_000,
    excess_liquidity: 200_000,
    buying_power: 0,
    dividends: 0,
    equity_with_loan: 500_000,
  },
};

async function setupMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  let ordersGetCount = 0;
  await page.route("**/api/orders", (route) => {
    if (route.request().method() !== "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(ordersPayload(BASELINE_FILLS)),
      });
    }
    ordersGetCount += 1;
    const executed = ordersGetCount === 1 ? BASELINE_FILLS : [...BASELINE_FILLS, NEW_FILL];
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ordersPayload(executed)),
    });
  });
  await page.route("**/api/portfolio**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_EMPTY),
    }),
  );
  await page.route("**/api/prices", (route) => route.abort());
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
      body: JSON.stringify({ connected: false }),
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
}

test.describe("Persistent fill toast", () => {
  test("initial load with existing today-fills → no toast storm", async ({ page }) => {
    await setupMocks(page);
    const firstOrdersGet = page.waitForResponse(
      (res) => res.url().includes("/api/orders") && res.request().method() === "GET",
    );
    await page.goto("/orders");
    await firstOrdersGet;

    // Give the priming effect a tick to run.
    await page.waitForTimeout(1_000);
    await expect(page.locator(".toast-container .toast.toast-success")).toHaveCount(0);
  });

  test("new fill on next poll → one persistent success toast, manual dismiss", async ({ page }) => {
    // The orders poll fires 30s after mount; budget one full tick plus slack.
    test.setTimeout(120_000);
    await setupMocks(page);
    await page.goto("/orders");

    const toasts = page.locator(".toast-container .toast.toast-success");
    const toast = toasts.first();
    await toast.waitFor({ timeout: 45_000 });

    await expect(toasts).toHaveCount(1);
    await expect(toast).toContainText("FILLED · SELL 25x EWY $175C @ $4.55");

    // Default toast duration is 5000ms; the fill toast fires with duration=0
    // (manual dismiss only). Wait past the default window and confirm it stays.
    await page.waitForTimeout(6_500);
    await expect(toasts).toHaveCount(1);
    await expect(toast).toBeVisible();

    await toast.locator(".toast-close").click();
    await expect(toasts).toHaveCount(0);
  });
});
