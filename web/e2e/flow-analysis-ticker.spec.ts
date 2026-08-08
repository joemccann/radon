/**
 * E2E: Per-ticker Flow Analysis route.
 *
 * Covers:
 *   1. Ticker input on /flow-analysis routes to /flow-analysis/{TICKER}
 *   2. Stale / missing cache triggers a POST scan and shows the analyzing state
 *   3. Fresh cache renders bullish/neutral/bearish badge + report sections
 */

import { test, expect, type Page } from "@playwright/test";

const PORTFOLIO = {
  bankroll: 1_500_000,
  peak_value: 1_500_000,
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
};

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

function bullishReport(ticker: string, fetchedAt: string) {
  return {
    ticker,
    fetched_at: fetchedAt,
    lookback_days: 20,
    verdict: { direction: "BULLISH", confidence: 78 },
    analysis: { signal: "STRONG", direction: "ACCUMULATION", strength: 78 },
    dark_pool: {
      aggregate: {
        flow_direction: "ACCUMULATION",
        flow_strength: 78,
        dp_buy_ratio: 0.69,
        total_volume: 12_345_000,
        total_premium: 8_910_000,
        buy_volume: 8_500_000,
        sell_volume: 3_845_000,
        num_prints: 450,
      },
      daily: [
        {
          date: "2026-05-08",
          flow_direction: "ACCUMULATION",
          flow_strength: 80,
          dp_buy_ratio: 0.7,
          num_prints: 110,
        },
        {
          date: "2026-05-07",
          flow_direction: "ACCUMULATION",
          flow_strength: 65,
          dp_buy_ratio: 0.625,
          num_prints: 90,
        },
      ],
    },
    options_flow: {
      bias: "BULLISH",
      put_call_ratio: 0.54,
      call_premium: 1_500_000,
      put_premium: 810_000,
      total_alerts: 24,
    },
    combined_signal: "STRONG_BULLISH_CONFLUENCE",
    market_status: "Market open (3.0h elapsed, 46% of day)",
    cache_meta: {
      last_refresh: fetchedAt,
      age_seconds: 30,
      is_stale: false,
      stale_threshold_seconds: 600,
    },
  };
}

async function setupBaseMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO) }),
  );
  await page.route("**/api/orders", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/regime", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/ib-status", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: true }) }),
  );
  await page.route("**/api/flow-analysis", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        analysis_time: new Date().toISOString(),
        positions_scanned: 0,
        supports: [],
        against: [],
        watch: [],
        neutral: [],
      }),
    }),
  );
  await page.route("**/api/service-health", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ services: [] }) }),
  );
}

test.describe("Flow Analysis per-ticker route", () => {
  test("ticker input on /flow-analysis navigates to /flow-analysis/{TICKER}", async ({ page }) => {
    await setupBaseMocks(page);
    await page.goto("/flow-analysis");

    const input = page.getByTestId("flow-ticker-input-field");
    await input.waitFor();
    await input.fill("aapl");
    await page.getByTestId("flow-ticker-input-submit").click();

    await expect(page).toHaveURL(/\/flow-analysis\/AAPL$/);
  });

  test("fresh cache renders the bullish badge + report sections", async ({ page }) => {
    await setupBaseMocks(page);
    const fresh = bullishReport("AAPL", new Date().toISOString());
    await page.route("**/api/flow-analysis/AAPL**", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fresh) }),
    );

    await page.goto("/flow-analysis/AAPL");

    const badge = page.getByTestId("ticker-flow-report").locator(".ticker-flow-badge");
    await badge.waitFor();
    await expect(badge).toHaveAttribute("data-direction", "BULLISH");
    await expect(badge).toContainText(/Bullish/i);
    await expect(badge).toContainText("78"); // confidence

    // Report sections render
    await expect(page.locator(".ticker-flow-report")).toContainText(/Dark Pool Aggregate/i);
    await expect(page.locator(".ticker-flow-report")).toContainText(/Options Flow Bias/i);
    await expect(page.locator(".ticker-flow-report")).toContainText("Put/Call Ratio");
    await expect(page.locator(".ticker-flow-report")).toContainText("0.54");
    await expect(page.locator(".ticker-flow-report")).not.toContainText("Call/Put Ratio");
    await expect(page.locator(".ticker-flow-report")).toContainText(/Daily Dark Pool History/i);
  });

  test("mobile options tab renders the put/call convention", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await setupBaseMocks(page);
    const fresh = bullishReport("AAPL", new Date().toISOString());
    await page.route("**/api/flow-analysis/AAPL**", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fresh) }),
    );

    await page.goto("/flow-analysis/AAPL");
    await page.getByRole("tab", { name: "Options" }).click();

    const report = page.getByTestId("ticker-flow-report");
    await expect(report).toContainText("P/C Ratio");
    await expect(report).toContainText("0.54");
    await expect(report).not.toContainText("C/P Ratio");
  });

  test("missing cache triggers a scan and shows the analyzing state", async ({ page }) => {
    await setupBaseMocks(page);

    let scanCalls = 0;
    await page.route("**/api/flow-analysis/NVDA**", async (route) => {
      const req = route.request();
      if (req.method() === "GET") {
        // Route now returns 200 + { missing: true } instead of 404 so the
        // browser console doesn't show a red error on the legitimate
        // first-time-scan path.
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ticker: "NVDA", missing: true, cache_meta: { is_stale: true } }),
        });
        return;
      }
      scanCalls += 1;
      // Add a slight delay so we can observe the analyzing state
      await new Promise((res) => setTimeout(res, 300));
      const report = bullishReport("NVDA", new Date().toISOString());
      report.verdict = { direction: "BEARISH", confidence: 60 };
      report.analysis = { signal: "STRONG", direction: "DISTRIBUTION", strength: 60 };
      report.dark_pool!.aggregate!.flow_direction = "DISTRIBUTION";
      report.combined_signal = "STRONG_BEARISH_CONFLUENCE";
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(report),
      });
    });

    await page.goto("/flow-analysis/NVDA");

    const analyzing = page.locator(".ticker-flow-analyzing-title");
    await expect(analyzing).toContainText(/Analyzing NVDA/i, { timeout: 5000 });

    // After scan completes, badge should resolve to BEARISH
    const badge = page.getByTestId("ticker-flow-report").locator(".ticker-flow-badge");
    await expect(badge).toHaveAttribute("data-direction", "BEARISH", { timeout: 5000 });
    await expect(badge).toContainText(/Bearish/i);

    expect(scanCalls).toBeGreaterThanOrEqual(1);
  });
});
