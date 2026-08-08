/**
 * E2E: Account and Risk metric cards on /portfolio
 *
 * Verifies:
 * - ACCOUNT row: NET LIQUIDATION, DAY P&L, DIVIDENDS cards are clickable
 * - RISK row: all 4 cards (BUYING POWER, MAINTENANCE MARGIN, EXCESS LIQUIDITY, SETTLED CASH) are clickable
 * - Clicking NET LIQUIDATION opens a modal containing "Net Liquidation" text and the formula
 * - All RISK section cards have the `metric-card-clickable` class
 *
 * Section collapse contract (components/MetricCards.tsx:552-559): only ACCOUNT
 * and TODAY'S P&L are expanded on mount; RISK / MARGIN / CAPITAL / EXPOSURE
 * start collapsed, so their cards are NOT in the DOM until the section header is
 * clicked. Every RISK assertion below therefore expands the section first via
 * `expandRiskSection()`. Collapse state is component-local `useState` with no
 * persistence, so each fresh `page.goto` starts collapsed again.
 */

import { test, expect } from "@playwright/test";

// ── Mock data ────────────────────────────────────────────────────────────────

const PORTFOLIO_MOCK = {
  bankroll: 1_131_051.65,
  peak_value: 1_131_051.65,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 5.78,
  total_deployed_dollars: 2_891.57,
  remaining_capacity_pct: 94.22,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [],
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 1_131_051.65,
    daily_pnl: -17_071.27,
    unrealized_pnl: -212_251.69,
    realized_pnl: -6_835.27,
    settled_cash: -14_654.04,
    maintenance_margin: 513_065.33,
    excess_liquidity: 185_943.44,
    buying_power: 743_773.78,
    dividends: 910.0,
  },
};

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

// ── Helpers ──────────────────────────────────────────────────────────────────

async function setupMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_MOCK),
    }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_EMPTY),
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

/** A metric card addressed by its `.metric-label` text. */
function metricCard(page: import("@playwright/test").Page, label: string) {
  return page
    .locator(".metric-card")
    .filter({ has: page.getByText(label, { exact: true }) })
    .first();
}

/** The single open explanation modal (components/Modal.tsx renders role="dialog"). */
function openModal(page: import("@playwright/test").Page) {
  return page.getByRole("dialog");
}

/**
 * Expand the collapsed RISK section.
 *
 * `SectionHeader` (components/MetricCards.tsx:78-94) is a bare <div> with an
 * onClick and no role/testid, so the toggle can only be addressed by its class
 * plus the exact section label. Suggested testid: `metric-section-toggle-risk`.
 */
async function expandRiskSection(page: import("@playwright/test").Page) {
  const riskToggle = page.locator(".section-label-toggle", { hasText: /^RISK$/ });
  await riskToggle.waitFor({ timeout: 10_000 });
  await riskToggle.click();
  await metricCard(page, "Buying Power").waitFor({ timeout: 10_000 });
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe("Account metric cards — clickable with explanation modals", () => {
  test("NET LIQUIDATION card has metric-card-clickable class", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    const card = metricCard(page, "Net Liquidation");
    await card.waitFor({ timeout: 10_000 });

    await expect(card).toHaveClass(/metric-card-clickable/);
  });

  test("DAY P&L card has metric-card-clickable class", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    const card = metricCard(page, "Day P&L");
    await card.waitFor({ timeout: 10_000 });

    await expect(card).toHaveClass(/metric-card-clickable/);
  });

  test("DIVIDENDS card has metric-card-clickable class", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    const card = metricCard(page, "Dividends");
    await card.waitFor({ timeout: 10_000 });

    await expect(card).toHaveClass(/metric-card-clickable/);
  });

  test("clicking NET LIQUIDATION opens modal with title and formula", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    const card = metricCard(page, "Net Liquidation");
    await card.waitFor({ timeout: 10_000 });
    await card.click();

    // Modal should appear
    const modal = openModal(page);
    await modal.waitFor({ timeout: 5_000 });
    await expect(modal).toBeVisible();

    // Title
    await expect(modal).toContainText("Net Liquidation Value");

    // Formula content
    await expect(modal).toContainText("reqAccountSummary");
    await expect(modal).toContainText("Cash");

    // Displays the value — fmtExact(1_131_051.65) per components/MetricCards.tsx:38
    await expect(modal).toContainText("1,131,051.65");
  });

  test("clicking DAY P&L opens modal with formula mentioning reqPnL", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    const card = metricCard(page, "Day P&L");
    await card.waitFor({ timeout: 10_000 });
    await card.click();

    const modal = openModal(page);
    await modal.waitFor({ timeout: 5_000 });
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Day P&L");
    await expect(modal).toContainText("reqPnL()");
  });

  test("clicking DIVIDENDS opens modal with formula mentioning DividendReceivedYear", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");

    const card = metricCard(page, "Dividends");
    await card.waitFor({ timeout: 10_000 });
    await card.click();

    const modal = openModal(page);
    await modal.waitFor({ timeout: 5_000 });
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Accrued Dividends");
    await expect(modal).toContainText("DividendReceivedYear");
  });
});

test.describe("Risk metric cards — all four are clickable with explanation modals", () => {
  test("BUYING POWER card has metric-card-clickable class", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");
    await expandRiskSection(page);

    await expect(metricCard(page, "Buying Power")).toHaveClass(/metric-card-clickable/);
  });

  test("MAINTENANCE MARGIN card has metric-card-clickable class", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");
    await expandRiskSection(page);

    await expect(metricCard(page, "Maintenance Margin")).toHaveClass(/metric-card-clickable/);
  });

  test("EXCESS LIQUIDITY card has metric-card-clickable class", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");
    await expandRiskSection(page);

    await expect(metricCard(page, "Excess Liquidity")).toHaveClass(/metric-card-clickable/);
  });

  test("SETTLED CASH card has metric-card-clickable class", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");
    await expandRiskSection(page);

    await expect(metricCard(page, "Settled Cash")).toHaveClass(/metric-card-clickable/);
  });

  test("clicking BUYING POWER opens modal with formula mentioning BuyingPower", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");
    await expandRiskSection(page);

    await metricCard(page, "Buying Power").click();

    const modal = openModal(page);
    await modal.waitFor({ timeout: 5_000 });
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Buying Power");
    await expect(modal).toContainText("BuyingPower");
    await expect(modal).toContainText("Excess Liquidity");
  });

  test("clicking MAINTENANCE MARGIN opens modal with margin call warning", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");
    await expandRiskSection(page);

    await metricCard(page, "Maintenance Margin").click();

    const modal = openModal(page);
    await modal.waitFor({ timeout: 5_000 });
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Maintenance Margin");
    await expect(modal).toContainText("MaintMarginReq");
    await expect(modal).toContainText("margin call");
  });

  test("clicking EXCESS LIQUIDITY opens modal with cushion formula", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");
    await expandRiskSection(page);

    await metricCard(page, "Excess Liquidity").click();

    const modal = openModal(page);
    await modal.waitFor({ timeout: 5_000 });
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Excess Liquidity");
    await expect(modal).toContainText("Net Liquidation");
    await expect(modal).toContainText("Maintenance Margin");
  });

  test("clicking SETTLED CASH opens modal with T+1/T+2 settlement note", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");
    await expandRiskSection(page);

    await metricCard(page, "Settled Cash").click();

    const modal = openModal(page);
    await modal.waitFor({ timeout: 5_000 });
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Settled Cash");
    await expect(modal).toContainText("T+1");
    await expect(modal).toContainText("T+2");
  });

  test("modal can be dismissed by clicking the close button", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/portfolio");
    await expandRiskSection(page);

    await metricCard(page, "Buying Power").click();

    const modal = openModal(page);
    await modal.waitFor({ timeout: 5_000 });
    await expect(modal).toBeVisible();

    // Close button is the aria-labelled control in components/Modal.tsx:86
    await modal.getByRole("button", { name: "Close" }).click();
    await expect(modal).not.toBeVisible();
  });
});
