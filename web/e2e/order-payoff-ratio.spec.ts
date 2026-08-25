/**
 * E2E: leveraged payoff on the order confirm panel.
 *
 * A spread's embedded leverage is invisible from its debit — the same outlay
 * can be a 7:1 or a 1.2:1 trade depending on width. The confirm step renders
 * "Payoff: N : 1" so the operator reads the multiple before execution instead
 * of dividing Max Gain by Max Loss in their head.
 *
 * Drives the SPXU cockpit combo ticket (same deep-link + portfolio mock as
 * e2e/order-combo.spec.ts) because it reaches `<OrderConfirmSummary>` through
 * a real surface without depending on live chain data.
 *
 * Two directions are covered deliberately:
 *   BUY  → opening spread, bounded both ways → the multiple renders.
 *   SELL → close-out (no new exposure, no max gain/loss) → row suppressed.
 */

import { test, expect } from "@playwright/test";

const SPXU_POSITION_ID = 23;

const PORTFOLIO_MOCK = {
  bankroll: 50_000,
  peak_value: 52_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 5.78,
  total_deployed_dollars: 2891.57,
  remaining_capacity_pct: 94.22,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [
    {
      id: SPXU_POSITION_ID,
      ticker: "SPXU",
      structure: "Bull Call Spread $53.0/$60.0",
      structure_type: "Bull Call Spread",
      risk_profile: "defined",
      expiry: "2026-03-13",
      contracts: 20,
      direction: "DEBIT",
      entry_cost: 2891.57,
      max_risk: 2891.57,
      market_value: 3950.0,
      market_price_is_calculated: false,
      legs: [
        {
          direction: "LONG",
          contracts: 20,
          type: "Call",
          strike: 53.0,
          entry_cost: 4079.75,
          avg_cost: 203.99,
          market_price: 2.875,
          market_value: 5750.0,
          market_price_is_calculated: false,
        },
        {
          direction: "SHORT",
          contracts: 20,
          type: "Call",
          strike: 60.0,
          entry_cost: 1188.18,
          avg_cost: 59.41,
          market_price: 0.9,
          market_value: 1800.0,
          market_price_is_calculated: false,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-09",
    },
  ],
  exposure: {},
  violations: [],
};

async function stubApis(page: import("@playwright/test").Page) {
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_MOCK) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        last_sync: new Date().toISOString(),
        open_orders: [],
        executed_orders: [],
        open_count: 0,
        executed_count: 0,
      }),
    }),
  );
  await page.route("**/api/prices", (route) => route.abort());
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 0.2 }) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
  );
  await page.route("**/api/ticker/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) }),
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

async function openTicket(page: import("@playwright/test").Page) {
  await page.goto(`/SPXU?posId=${SPXU_POSITION_ID}`);
  const ticket = page.locator(".act-ticket");
  await ticket.waitFor({ timeout: 30_000 });
  // The combo submit exists only once the held combo resolves; gating on it
  // guarantees every interaction lands on the combo ticket, not the flat form.
  await ticket.getByRole("button", { name: "Place Combo Order" }).waitFor({ timeout: 30_000 });
  return ticket;
}

function dollars(text: string): number {
  return Number(text.replace(/[^0-9.-]/g, ""));
}

test.describe("Order confirm — leveraged payoff", () => {
  test.setTimeout(90_000);

  test.beforeEach(async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await page.addInitScript(() => {
      window.addEventListener("error", (e) => e.preventDefault());
    });
    await stubApis(page);
  });

  test("opening bull call spread renders a Payoff multiple matching max gain / max loss", async ({
    page,
  }, testInfo) => {
    const ticket = await openTicket(page);

    // BUY = opening the spread (the ticket defaults to SELL-to-close).
    await ticket.getByRole("button", { name: "BUY", exact: true }).click();
    const price = ticket.locator(".modify-price-input").first();
    await price.waitFor({ timeout: 10_000 });
    await price.fill("1.50");
    await ticket.getByRole("button", { name: "Place Combo Order" }).click();

    const summary = ticket.locator(".order-confirm-summary");
    await expect(summary).toBeVisible();

    const payoff = summary.locator('[data-testid="order-payoff-ratio"]');
    await expect(payoff).toBeVisible();

    const summaryText = (await summary.textContent()) ?? "";
    const maxGain = dollars(/Max Gain:(\$[\d,]+)/.exec(summaryText)?.[1] ?? "");
    const maxLoss = dollars(/Max Loss:(\$[\d,]+)/.exec(summaryText)?.[1] ?? "");
    expect(maxGain).toBeGreaterThan(0);
    expect(maxLoss).toBeGreaterThan(0);

    // The rendered multiple must agree with the numbers beside it — this is
    // the assertion that catches a payoff row wired to the wrong field.
    const payoffText = ((await payoff.textContent()) ?? "").trim();
    expect(payoffText).toMatch(/^\d+(\.\d)? : 1/);
    const renderedRatio = Number(payoffText.split(":")[0].trim());
    expect(renderedRatio).toBeCloseTo(Math.round((maxGain / maxLoss) * 10) / 10, 1);

    // A $7-wide spread bought for $1.50 clears Gate 1 convexity (>= 2x).
    expect(maxGain / maxLoss).toBeGreaterThan(2);
    await expect(payoff).toHaveAttribute("data-meets-convexity", "true");

    await summary.screenshot({ path: testInfo.outputPath("order-payoff-bull-call.png") });
  });

  test("close-out order shows no payoff multiple", async ({ page }) => {
    const ticket = await openTicket(page);

    // Default action is SELL — closing the held spread. A close adds no new
    // exposure, so there is no max gain / max loss to divide.
    const price = ticket.locator(".modify-price-input").first();
    await price.waitFor({ timeout: 10_000 });
    await price.fill("1.50");
    await ticket.getByRole("button", { name: "Place Combo Order" }).click();

    const summary = ticket.locator(".order-confirm-summary");
    await expect(summary).toBeVisible();
    await expect(summary).toContainText("Est. Realized P&L:");
    await expect(summary.locator('[data-testid="order-payoff-ratio"]')).toHaveCount(0);
  });
});
