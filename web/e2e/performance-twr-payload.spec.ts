import { expect, test } from "@playwright/test";

// ---------------------------------------------------------------------------
// Window-relative dates. Staleness is decided at READ time from nav_as_of and
// the clock (performanceData.ts resolveSessionsBehind), so a hardcoded
// calendar date rots past NAV_STALENESS_BUDGET_SESSIONS and nulls the hero.
// nav_as_of = today can never be behind the last completed session.
// ---------------------------------------------------------------------------
const DAY_MS = 86_400_000;
const isoDaysAgo = (days: number): string => new Date(Date.now() - days * DAY_MS).toISOString().slice(0, 10);

const PERIOD_END = isoDaysAgo(0);
const PERIOD_MID = isoDaysAgo(45);
const PERIOD_START = isoDaysAgo(90);

type GatedValue = {
  value: number | null;
  n: number;
  min_n: number;
  unavailable_reason: string | null;
  low_confidence?: boolean;
};

function gated(value: number | null, n: number, minN: number, reason: string | null): GatedValue {
  return { value, n, min_n: minN, unavailable_reason: reason };
}

/**
 * A payload the v2 integrity gates would publish: schema_version 2 (the only
 * evidence the gates ran — isV2Payload), declared status "ok", flows verified
 * ("ok", not "failed"), NAV as of today. Under buildPerformanceView this must
 * survive every hero suppression (flows failed / degraded / stale /
 * implausible) and render twr.cum_return.
 */
function v2OkPayload() {
  return {
    schema_version: 2,
    status: "ok",
    generated_at: `${PERIOD_END}T20:05:00.000000Z`,
    account_id: "U0000000",
    methodology: {
      curve_type: "twr_daily_eod",
      return_basis: "time_weighted",
      flow_convention: "bod",
      day_count: "act/365",
      vol_scaling_days: 252,
      sortino_target: 0,
      risk_free_rate: 0.0412,
      risk_free_source: "fred_dgs3mo",
      benchmark_basis: "price_return",
      inferred_flows: [],
    },
    nav_source: "flex_live",
    nav_as_of: PERIOD_END,
    nav_sessions_behind: 0,
    flows_status: "ok",
    flows_source: "flex_cash_transactions+transfers",
    period_start: PERIOD_START,
    period_end: PERIOD_END,
    calendar_days: 90,
    counts: { n_nav_observations: 58, n_subperiods: 57, n_returns: 57, n_skipped: 0, n_suspect: 0 },
    twr: {
      // +10% over 90 days annualizes to ~47%/yr — inside the IMPLAUSIBLE_ANNUALIZED gate.
      cum_return: 0.1,
      annualized: gated(null, 90, 365, "period_lt_1y"),
      excludes_suspect: false,
    },
    mwr: {
      period_return: gated(null, 57, 20, "not_computed"),
      annualized: gated(null, 90, 365, "period_lt_1y"),
      multiple_sign_changes: false,
    },
    risk: {
      volatility: gated(0.09, 57, 20, null),
      max_drawdown: gated(-0.05, 57, 20, null),
      current_drawdown: gated(0, 57, 20, null),
    },
    distribution: {},
    drawdown_detail: {},
    equity: { starting: 100_000, ending: 110_000, net_external_flows: 0, investment_pnl: 10_000 },
    benchmark: null,
    subperiods: [],
    warnings: [],
    series: [
      { date: PERIOD_START, nav: 100_000, twr_index: 100, daily_return: null, cum_return: 0, drawdown: 0, flow: 0, skipped: false },
      { date: PERIOD_MID, nav: 105_000, twr_index: 105, daily_return: 0.05, cum_return: 0.05, drawdown: 0, flow: 0, skipped: false },
      { date: PERIOD_END, nav: 110_000, twr_index: 110, daily_return: 0.047_619, cum_return: 0.1, drawdown: 0, flow: 0, skipped: false },
    ],
  };
}

/** The same snapshot with the one field the gates stamp removed. Without
 *  schema_version the payload is legacy: resolveStatus degrades it regardless
 *  of its own declared "ok", and the hero must refuse to print a number. */
function legacyPayloadWithoutSchemaVersion() {
  const { schema_version: _dropped, ...rest } = v2OkPayload();
  return rest;
}

const PORTFOLIO_EMPTY = {
  bankroll: 100_000,
  peak_value: 100_000,
  last_sync: `${PERIOD_END}T18:55:00Z`,
  positions: [],
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  account_summary: {
    net_liquidation: 100_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 100_000,
    dividends: 0,
  },
};

async function openPerformanceWith(page: import("@playwright/test").Page, performancePayload: unknown) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/performance", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(performancePayload) }),
  );
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        last_sync: `${PERIOD_END}T18:55:00Z`,
        open_orders: [],
        executed_orders: [],
        open_count: 0,
        executed_count: 0,
      }),
    }),
  );
  await page.goto("/performance");
  await expect(page.locator('[data-testid="performance-panel"]')).toBeVisible({ timeout: 15_000 });
}

test.describe("/performance TWR payload contract", () => {
  test("a valid v2 payload renders the cumulative TWR in the hero", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await openPerformanceWith(page, v2OkPayload());

    await expect(page.locator('[data-testid="performance-hero-twr"]')).toHaveText("+10.00%");
    await expect(page.locator('[data-testid="performance-hero-subtitle"]')).toContainText("Ending equity $110,000.00");
    await expect(page.getByText("RUNTIME ERROR")).toHaveCount(0);
    expect(pageErrors.filter((message) => message.includes("length") || message.includes("toUpperCase"))).toEqual([]);
    await page.screenshot({ path: "test-results/performance-twr-payload.png", fullPage: true });
  });

  test("a payload without schema_version degrades to the honest --", async ({ page }) => {
    await openPerformanceWith(page, legacyPayloadWithoutSchemaVersion());

    await expect(page.locator('[data-testid="performance-hero-twr"]')).toHaveText("--");
  });
});
