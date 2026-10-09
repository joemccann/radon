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

  test("tapping a closed fill expands share actions that request the P&L card", async ({ page }, testInfo) => {
    await setupBaseMocks(page);
    const closingFill = {
      ...ORDERS_WITH_FILL.executed_orders[0],
      execId: "exec-close",
      side: "SLD",
      avgPrice: 6.55,
      realizedPNL: 1520.25,
      time: `${TODAY}T15:30:00Z`,
    };
    await page.route("**/api/orders", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...ORDERS_WITH_FILL, executed_count: 1, executed_orders: [closingFill] }) }),
    );
    const shareRequests: string[] = [];
    await page.route("**/api/share/pnl**", (route) => {
      shareRequests.push(route.request().url());
      return route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from([0x89, 0x50, 0x4e, 0x47]) });
    });
    await page.goto("/orders");

    const card = page.getByTestId("mobile-executed-list").locator('[aria-label="AAPL CLOSE"]');
    await expect(card).toBeVisible();
    await expect(page.locator('[data-testid$="-share"]')).toHaveCount(0);
    await card.click();
    await expect(card).toHaveAttribute("aria-expanded", "true");
    const panel = page.locator('[data-testid$="-share"]');
    await expect(card.locator('[data-testid$="-share"]')).toHaveCount(0);
    await expect(panel.getByRole("button", { name: "Copy & Tweet" })).toBeVisible();
    await panel.getByRole("checkbox", { name: "P&L $" }).click();
    await expect(card).toHaveAttribute("aria-expanded", "true");
    expect(shareRequests).toHaveLength(0);
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: { write: async () => undefined } });
    });
    await panel.getByRole("button", { name: "Copy", exact: true }).click();
    await expect.poll(() => shareRequests.length).toBe(1);
    const url = new URL(shareRequests[0]);
    expect(url.pathname).toBe("/api/share/pnl");
    expect(url.searchParams.get("pnl")).toBe("1520.25");
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("mobile-executed-share.png") });
  });

  test("a tall close keeps Story above the fills and hands the PNG to the share sheet", async ({ page }, testInfo) => {
    await page.addInitScript(() => {
      const w = window as unknown as { __shared: { name: string; type: string }[] };
      w.__shared = [];
      Object.defineProperty(navigator, "canShare", { configurable: true, value: () => true });
      Object.defineProperty(navigator, "share", {
        configurable: true,
        value: async (data: ShareData) => {
          for (const file of data.files ?? []) w.__shared.push({ name: file.name, type: file.type });
        },
      });
    });
    await setupBaseMocks(page);
    const legs = Array.from({ length: 8 }, (_, i) => ({
      execId: `exec-aaoi-${i}`,
      symbol: "AAOI",
      contract: {
        conId: 5000 + i,
        symbol: "AAOI",
        secType: "OPT",
        strike: i % 2 === 0 ? 110 : 113,
        right: i % 2 === 0 ? "P" : "C",
        expiry: "20261009",
      },
      side: "SLD",
      quantity: 1,
      avgPrice: 0.35,
      commission: 0.65,
      realizedPNL: 250.5,
      time: `${TODAY}T15:30:0${i}Z`,
      exchange: "SMART",
      permId: 2011740827,
    }));
    await page.route("**/api/orders", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...ORDERS_WITH_FILL, executed_count: legs.length, executed_orders: legs }),
      }),
    );
    await page.route("**/api/share/pnl**", (route) =>
      route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from([0x89, 0x50, 0x4e, 0x47]) }),
    );
    await page.goto("/orders");

    const card = page.getByTestId("mobile-executed-list").locator('[aria-label="AAOI CLOSE"]');
    await expect(card).toBeVisible();
    await card.click();
    const story = page.getByRole("button", { name: "Instagram Story" });
    const fills = page.locator('[data-testid$="-fills"]');
    await expect(story).toBeVisible();
    await expect(fills).toBeVisible();
    await expect(card.locator('[data-testid$="-share"]')).toHaveCount(0);
    const storyBox = await story.boundingBox();
    const fillsBox = await fills.boundingBox();
    expect(storyBox).not.toBeNull();
    expect(fillsBox).not.toBeNull();
    expect(storyBox!.y + storyBox!.height).toBeLessThanOrEqual(fillsBox!.y + 1);
    await story.click();
    await expect(card).toHaveAttribute("aria-expanded", "true");
    await expect.poll(() => page.evaluate(() => (window as unknown as { __shared: { name: string; type: string }[] }).__shared)).toEqual([
      { name: "radon-pnl-story.png", type: "image/png" },
    ]);
    await story.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("mobile-executed-story.png") });
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
