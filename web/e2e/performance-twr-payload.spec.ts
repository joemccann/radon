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

/** Compressed, sanitized full-window NAV sample, not the user's account tape.
 * The two NAV jumps reproduce the incident; only the independently recorded
 * deposit/transfer amounts count as flows. The remaining observations keep
 * the recovered beginning-of-day TWR positive without treating a wire as P&L.
 */
function historicalFlowPayload(repaired: boolean) {
  const dates = ["2025-12-31", "2026-01-12", "2026-01-13", "2026-02-05", "2026-02-06", "2026-09-14", "2026-09-15"];
  const navs = [200_000, 200_000, 279_074.84, 246_713.5, 972_215.53, 1_030_000, 1_040_000];
  const verifiedFlows = [0, 0, 80_007.13, 0, 655_497.16, 0, 0];
  let index = 100;
  let peak = 100;
  const series = dates.map((date, i) => {
    const skipped = !repaired && verifiedFlows[i] !== 0;
    const flow = repaired ? verifiedFlows[i] : 0;
    const dailyReturn = i === 0 || skipped ? null : (navs[i] - navs[i - 1] - flow) / (navs[i - 1] + flow);
    if (dailyReturn !== null) index *= 1 + dailyReturn;
    peak = Math.max(peak, index);
    return { date, nav: navs[i], twr_index: index, daily_return: dailyReturn, cum_return: index / 100 - 1, drawdown: index / peak - 1, flow, skipped };
  });
  const n = repaired ? 6 : 4;
  return {
    ...v2OkPayload(),
    status: repaired ? "ok" : "degraded",
    generated_at: "2026-09-16T12:00:00Z",
    nav_as_of: "2026-09-15",
    period_start: "2025-12-31",
    period_end: "2026-09-15",
    period_label: "Since first NAV",
    calendar_days: 258,
    flows_source: repaired ? "flex_cash_transactions+transfers+turso" : "flex_cash_transactions+transfers",
    counts: { n_nav_observations: 7, n_subperiods: 6, n_returns: n, n_skipped: repaired ? 0 : 2, n_suspect: repaired ? 0 : 2 },
    twr: { cum_return: repaired ? 0.01566201246168375 : null, annualized: gated(null, 258, 365, "period_lt_1y"), excludes_suspect: !repaired },
    mwr: { period_return: gated(null, n, 20, "insufficient_n"), annualized: gated(null, 258, 365, "period_lt_1y"), multiple_sign_changes: false },
    risk: {},
    equity: { starting: 200_000, ending: 1_040_000, net_external_flows: repaired ? 735_504.29 : 0, investment_pnl: repaired ? 104_495.71 : null },
    series,
    subperiods: series.slice(1).map((point, i) => ({ date: point.date, b: navs[i], e: point.nav, c: point.flow, r: point.daily_return, flags: point.skipped ? ["suspect"] : [], skip_reason: point.skipped ? "suspect_no_flow" : null })),
    warnings: repaired ? [] : [
      { code: "SUBPERIOD_SUSPECT", severity: "error", message: "2026-01-13 moved +79,074.84 with no recorded external flow; excluded from the chain.", context: { date: "2026-01-13", flow: 0 } },
      { code: "SUBPERIOD_SUSPECT", severity: "error", message: "2026-02-06 moved +725,502.03 with no recorded external flow; excluded from the chain.", context: { date: "2026-02-06", flow: 0 } },
    ],
  };
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

  test("restored historical flows recover the full NAV window after refresh without relaxing the missing-flow guard", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.clock.setFixedTime(new Date("2026-09-16T13:00:00Z"));
    let repaired = false;
    let performanceReads = 0;
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    // Keep this regression isolated from broker, database and external feeds.
    await page.route("**/api/**", (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = { error: "Unavailable in isolated performance fixture" };
      let status = 503;
      if (path === "/api/performance") {
        performanceReads += 1;
        body = historicalFlowPayload(repaired);
        status = 200;
      } else if (path === "/api/portfolio") {
        body = { ...PORTFOLIO_EMPTY, last_sync: "2026-09-16T12:00:00Z" };
        status = 200;
      } else if (path === "/api/orders") {
        body = { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 };
        status = 200;
      }
      return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto("/performance");
    const hero = page.getByTestId("performance-hero-twr");
    const banner = page.getByTestId("performance-degraded-banner");
    await expect(hero).toHaveText("--");
    await expect(banner).toContainText("2026-01-13 moved +79,074.84 with no recorded external flow");
    await expect(banner).toContainText("2026-02-06 moved +725,502.03 with no recorded external flow");
    await expect(page.getByTestId("performance-line-equity")).toHaveAttribute("d", "");
    await expect(page.getByTestId("performance-period-label")).toContainText("2025-12-31 to 2026-09-15");
    await page.screenshot({ path: testInfo.outputPath("performance-flow-history-degraded.png"), fullPage: true });

    const readsBefore = performanceReads;
    repaired = true;
    await page.reload();
    await expect.poll(() => performanceReads).toBeGreaterThan(readsBefore);
    await expect(hero).toHaveText("+1.57%");
    await expect(banner).toHaveCount(0);
    await expect(page.getByTestId("performance-stale-banner")).toHaveCount(0);
    await expect(page.getByTestId("performance-period-label")).toContainText("2025-12-31 to 2026-09-15");
    await expect(page.getByTestId("performance-hero-subtitle")).toContainText("Ending equity $1,040,000.00 / as of 2026-09-15 / N=6");
    await expect(page.getByText("External Flows", { exact: true }).locator("..")).toContainText("$735,504.29");
    await expect(page.getByTestId("performance-line-equity")).toHaveAttribute("d", /M.+L/);
    await expect(page.getByText(/SUBPERIOD_SUSPECT:/)).toHaveCount(0);
    expect(pageErrors).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("performance-flow-history-repaired.png"), fullPage: true });
  });
});
