/**
 * E2E: Daily Dark Pool History on the mobile shell (393×852).
 *
 * Bug (PLTR HISTORY screenshot): the 5-col DATE / DIRECTION / STRENGTH /
 * BUY % / PRINTS table was a desktop table with no overflow strategy.
 * PRINTS clipped at the card edge; dates wrapped; the page scrolled sideways.
 *
 * Invariant: the page body never scrolls horizontally; the table scrolls
 * inside the shared `.table-wrap`; DATE stays sticky; PRINTS is fully
 * readable after scrolling the wrapper.
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

const DAILY = [
  { date: "2026-09-11", flow_direction: "DISTRIBUTION", flow_strength: 30.2, dp_buy_ratio: 0.35, num_prints: 73000 },
  { date: "2026-09-10", flow_direction: "DISTRIBUTION", flow_strength: 34.5, dp_buy_ratio: 0.33, num_prints: 59855 },
  { date: "2026-09-09", flow_direction: "DISTRIBUTION", flow_strength: 38.2, dp_buy_ratio: 0.31, num_prints: 42300 },
  { date: "2026-09-08", flow_direction: "DISTRIBUTION", flow_strength: 37.6, dp_buy_ratio: 0.31, num_prints: 21668 },
  { date: "2026-09-04", flow_direction: "ACCUMULATION", flow_strength: 19.9, dp_buy_ratio: 0.60, num_prints: 19183 },
  { date: "2026-09-03", flow_direction: "ACCUMULATION", flow_strength: 18.3, dp_buy_ratio: 0.59, num_prints: 16378 },
  { date: "2026-09-02", flow_direction: "ACCUMULATION", flow_strength: 12.6, dp_buy_ratio: 0.56, num_prints: 11249 },
];

function pltrReport(fetchedAt: string) {
  return {
    ticker: "PLTR",
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
      daily: DAILY,
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

async function setupMocks(page: Page) {
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
  await page.route("**/api/flow-analysis/PLTR**", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(pltrReport(new Date().toISOString())),
    }),
  );
  await page.route("**/api/prices", (r) => r.abort());
}

async function openHistory(page: Page) {
  await setupMocks(page);
  await page.goto("/flow-analysis/PLTR");
  await page.getByRole("tab", { name: "History" }).click();
  const history = page.getByTestId("daily-dp-history");
  await history.waitFor();
  return history;
}

test.use({ viewport: { width: 393, height: 852 } });

test.describe("daily dark pool history — mobile shell", () => {
  test("the session table scrolls in its wrapper, never the page", async ({ page }) => {
    await openHistory(page);

    const overflow = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(
      overflow.scrollWidth,
      `document scrolls sideways (${overflow.scrollWidth}px > ${overflow.innerWidth}px viewport)`,
    ).toBeLessThanOrEqual(overflow.innerWidth + 1);

    const wrap = page.getByTestId("daily-dp-history-table-wrap");
    const wrapperState = await wrap.evaluate((el) => ({
      overflowX: window.getComputedStyle(el).overflowX,
      clientWidth: el.clientWidth,
      scrollWidth: el.scrollWidth,
    }));
    expect(wrapperState.overflowX).toBe("auto");
    expect(wrapperState.scrollWidth).toBeGreaterThan(wrapperState.clientWidth);
  });

  test("DATE stays in view and PRINTS is fully readable after a horizontal scroll", async ({ page }) => {
    await openHistory(page);

    const wrap = page.getByTestId("daily-dp-history-table-wrap");
    const table = wrap.locator("table.ticker-flow-daily");
    await expect(table.getByRole("columnheader", { name: /date/i })).toBeVisible();
    await expect(table.getByRole("columnheader", { name: /prints/i })).toBeVisible();

    const dateCell = table.locator("tbody tr").first().locator("td").first();
    const printsCell = table.locator("tbody tr").first().locator("td").last();
    await expect(dateCell).toHaveText("2026-09-11");
    await expect(printsCell).toHaveText("73000");

    const atRest = await dateCell.evaluate((el) => {
      const rect = el.getBoundingClientRect();
      return { left: rect.left, right: rect.right, width: rect.width };
    });
    expect(atRest.left).toBeGreaterThanOrEqual(0);
    expect(atRest.right).toBeLessThanOrEqual(393);

    await wrap.evaluate((el) => {
      el.scrollLeft = el.scrollWidth;
    });

    const afterScroll = await wrap.evaluate((el) => {
      const date = el.querySelector("tbody tr td:first-child") as HTMLElement;
      const prints = el.querySelector("tbody tr td:last-child") as HTMLElement;
      const wrapRect = el.getBoundingClientRect();
      const dateRect = date.getBoundingClientRect();
      const printsRect = prints.getBoundingClientRect();
      const dateSticky = window.getComputedStyle(date);
      return {
        dateLeft: dateRect.left,
        dateRight: dateRect.right,
        datePosition: dateSticky.position,
        dateLeftCss: dateSticky.left,
        printsLeft: printsRect.left,
        printsRight: printsRect.right,
        wrapLeft: wrapRect.left,
        wrapRight: wrapRect.right,
        printsText: prints.textContent,
      };
    });

    expect(afterScroll.datePosition).toBe("sticky");
    expect(afterScroll.dateLeftCss).toBe("0px");
    expect(afterScroll.dateLeft).toBeGreaterThanOrEqual(afterScroll.wrapLeft - 1);
    expect(afterScroll.dateRight).toBeLessThan(afterScroll.wrapRight);
    expect(afterScroll.printsText).toBe("73000");
    expect(afterScroll.printsLeft).toBeGreaterThanOrEqual(afterScroll.wrapLeft);
    expect(afterScroll.printsRight).toBeLessThanOrEqual(afterScroll.wrapRight + 1);
  });
});
