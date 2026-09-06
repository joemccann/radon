/**
 * E2E: Today's Executed Orders + standalone Journal page mobile cards at 393×852.
 *
 * Validates:
 * 1. /orders → MobileExecutedList renders one card per fill group when fills exist.
 * 2. /journal → MobileJournalList renders one card per trade.
 * 3. The desktop tables for both are hidden on mobile.
 */

import { test, expect, type Page } from "@playwright/test";
import type { CashFlowResponse } from "../lib/useCashFlows";

const TODAY = "2026-05-06";
const CASH_FLOWS_EMPTY = {
  rows: [], count: 0, from_date: "2026-02-05",
  summary: { deposits: 0, withdrawals: 0, dividends: 0, net: 0 },
  last_synced_at: null,
} satisfies CashFlowResponse;

const PORTFOLIO = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: `${TODAY}T14:34:25Z`,
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

const ORDERS_WITH_FILL = {
  open_count: 0,
  executed_count: 1,
  open_orders: [],
  executed_orders: [
    {
      execId: "exec-1",
      symbol: "AAPL",
      contract: { conId: 12345, symbol: "AAPL", secType: "OPT", strike: 200, right: "C", expiry: "20260619" },
      side: "BOT",
      quantity: 5,
      avgPrice: 3.5,
      commission: 0.5,
      realizedPNL: null,
      time: `${TODAY}T15:00:00Z`,
      exchange: "SMART",
    },
  ],
  last_sync: `${TODAY}T14:34:25Z`,
};

const JOURNAL = {
  trades: [
    {
      id: 42,
      date: TODAY,
      ticker: "MSFT",
      structure: "Long Call ($410)",
      decision: "OPEN",
      contracts: 3,
      entry_cost: 900,
      max_risk: 900,
      realized_pnl: null,
      return_on_risk: null,
      legs: [],
    },
    {
      id: 41,
      date: "2026-04-20",
      close_date: "2026-04-25",
      ticker: "NVDA",
      structure: "Bull Call Spread",
      decision: "CLOSED",
      contracts: 5,
      entry_cost: 1200,
      max_risk: 1200,
      realized_pnl: 380,
      return_on_risk: 0.317,
      legs: [],
    },
  ],
};

async function setupBaseMocks(page: Page) {
  await page.clock.setFixedTime(new Date(`${TODAY}T16:00:00Z`));
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/**", (route) => route.fulfill({ status: 503, json: { error: "Unmocked test endpoint" } }));
  await page.route("**/api/portfolio**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: `${TODAY}T14:34:25Z`, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  await page.route("**/api/cash-flows**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CASH_FLOWS_EMPTY) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

test.describe("Mobile executed orders", () => {
  test("MobileExecutedList renders a card for each fill group", async ({ page }, testInfo) => {
    await setupBaseMocks(page);
    await page.route("**/api/orders", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_WITH_FILL) }),
    );
    await page.goto("/orders");

    const list = page.getByTestId("mobile-executed-list");
    await expect(list).toBeVisible();
    const card = list.locator('[data-testid^="mobile-executed-"]');
    await expect(card).toHaveCount(1);
    await expect(card).toBeVisible();
    await expect(card).toContainText("AAPL");
    await expect(card).toContainText("OPEN");
    await expect(card.getByText("5", { exact: true })).toBeVisible();
    await expect(card.getByText("$3.50", { exact: true })).toBeVisible();
    await expect(card.getByText("$0.50", { exact: true })).toBeVisible();
    await expect(card).toContainText("11:00:00 AM");
    await expect(page.getByText(/NaN/)).toHaveCount(0);
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("mobile-executed.png") });
  });

  test("desktop executed-orders table is hidden on mobile", async ({ page }) => {
    await setupBaseMocks(page);
    await page.route("**/api/orders", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_WITH_FILL) }),
    );
    await page.goto("/orders");

    await expect(page.getByTestId("mobile-executed-list")).toBeVisible();
    const execTables = page.locator("table").filter({ hasText: "Net Price" });
    await expect(execTables).toHaveCount(0);
  });
});

test.describe("Mobile journal page", () => {
  test("MobileJournalList renders a card per trade", async ({ page }, testInfo) => {
    await setupBaseMocks(page);
    await page.route("**/api/orders", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ open_count: 0, executed_count: 0, open_orders: [], executed_orders: [], last_sync: `${TODAY}T14:34:25Z` }) }),
    );
    await page.route("**/api/journal", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(JOURNAL) }),
    );
    await page.goto("/journal");

    // MTD includes the May open but excludes the April close. Widen the
    // range through the actual control before asserting the full journal.
    await expect(page.getByTestId("journal-range-mtd")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("mobile-journal-42")).toBeVisible();
    await expect(page.getByTestId("mobile-journal-41")).toHaveCount(0);
    await page.getByTestId("journal-range-all").click();
    await expect(page.getByTestId("journal-range-all")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("mobile-journal-list")).toBeVisible();
    await expect(page.getByTestId("mobile-journal-42")).toBeVisible();
    await expect(page.getByTestId("mobile-journal-41")).toBeVisible();

    const closedCard = page.getByTestId("mobile-journal-41");
    await expect(closedCard).toContainText("NVDA");
    await expect(closedCard).toContainText("CLOSED");
    await expect(closedCard).toContainText("+$380");
    await expect(closedCard).toContainText("+31.7%");
    await page.getByRole("toolbar", { name: "Sort trades" }).getByRole("button", { name: "P&L", exact: true }).click();
    await expect(page.getByTestId("mobile-journal-list").locator('.mobile-card-list > [data-testid]').first()).toHaveAttribute("data-testid", "mobile-journal-41");
    await closedCard.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("mobile-journal.png") });
  });

  test("desktop journal table is hidden on mobile", async ({ page }) => {
    await setupBaseMocks(page);
    await page.route("**/api/orders", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ open_count: 0, executed_count: 0, open_orders: [], executed_orders: [], last_sync: `${TODAY}T14:34:25Z` }) }),
    );
    await page.route("**/api/journal", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(JOURNAL) }),
    );
    await page.goto("/journal");

    await page.getByTestId("journal-range-all").click();
    await expect(page.getByTestId("mobile-journal-list")).toBeVisible();
    const journalTables = page.locator("table");
    await expect(journalTables).toHaveCount(0);
  });
});
