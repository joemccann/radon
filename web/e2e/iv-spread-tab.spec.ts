import { test, expect, type Page } from "@playwright/test";

// Spec: docs/indicators/iv-spread.md section H.5.
//
// Pins the /regime/iv-spread contract against a payload this file owns: tab
// activation, the summary strip, the freshness rail, the chart with its
// brush, and the missing state. Ambient routes (portfolio, orders, ib-status)
// are mocked and the prices stream aborted so the workspace shell settles
// without a backend.
//
// Not in CI. Run locally:
//   cd web && PLAYWRIGHT_PORT=3033 RADON_AUTHLESS_TEST=1 npx playwright test iv-spread-tab

// ~320 sessions ending 2026-09-02. One session in the middle is excluded by
// the bad-print gate (spread null): the chart must gap, never zero.
function buildSeries() {
  const series: Array<{
    date: string;
    spx_iv: number;
    ndx_iv: number;
    spread: number | null;
  }> = [];
  const day = new Date(Date.UTC(2025, 5, 10));
  let i = 0;
  while (series.length < 320) {
    const dow = day.getUTCDay();
    if (dow !== 0 && dow !== 6) {
      const spx = 0.12 + (i % 13) * 0.002;
      const ndx = 0.17 + (i % 11) * 0.003;
      series.push({
        date: day.toISOString().slice(0, 10),
        spx_iv: spx,
        ndx_iv: ndx,
        spread: i === 160 ? null : (ndx - spx) * 100,
      });
      i += 1;
    }
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return series;
}

const SERIES = buildSeries();
const LAST = SERIES[SERIES.length - 1];

const IV_SPREAD_MOCK = {
  scan_time: new Date().toISOString(),
  status: "ok",
  source: "ib",
  as_of: LAST.date,
  expected_session: LAST.date,
  market_status: "closed",
  count: SERIES.length,
  spread_count: SERIES.filter((entry) => entry.spread != null).length,
  dropped_unpaired: 0,
  current: {
    date: LAST.date,
    spx_iv: 0.12104312,
    ndx_iv: 0.1758578,
    spread: 5.481468,
    z_score: 0.104002,
    pctile: 59.377494,
    change_1d: 0.360352,
    regime: "NORMAL",
  },
  stats: {
    count: 1253,
    high: 12.642458,
    high_date: "2026-06-23",
    low: -3.297135,
    low_date: "2025-04-08",
    mean: 5.318448,
    stdev: 1.567474,
    last: 5.481468,
  },
  excluded: [],
  series: SERIES,
};

const MISSING_IV_SPREAD = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  count: 0,
  series: [],
  current: null,
  stats: null,
  excluded: [],
};

// The instant the freshness-rail assertions are written against, shared with
// web/tests/freshness-rail.test.ts: 2026-08-26 is a Wednesday and 21:00 UTC is
// 17:00 ET, after the close and before the 22:15 UTC radon-iv-spread.timer slot.
const RAIL_NOW = "2026-08-26T21:00:00Z";
// The session the panel holds: Tuesday, one behind the close that just printed.
const RAIL_AS_OF = "2026-08-25";

const RAIL_PAYLOAD = {
  ...IV_SPREAD_MOCK,
  scan_time: RAIL_NOW,
  as_of: RAIL_AS_OF,
  expected_session: "2026-08-26",
  current: { ...IV_SPREAD_MOCK.current, date: RAIL_AS_OF },
};

const PORTFOLIO_EMPTY = {
  bankroll: 100_000,
  positions: [],
  account_summary: {},
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

// playwright.config.ts sets `extraHTTPHeaders: { "x-radon-authless-test": … }`
// on EVERY request, cross-origin ones included. A custom header on a
// cross-origin script fetch forces a CORS preflight, and Clerk's CDN answers
// the preflight with its version-pin 307 - which is not allowed for a
// preflight, so clerk-js never loads and the whole tree never hydrates.
// Refetch the Clerk assets without the header and hand them back with a
// permissive ACAO so the page hydrates and the panel's own fetch fires.
async function letClerkLoad(page: Page) {
  await page.route(/clerk\.accounts\.dev/, async (route) => {
    const headers = { ...route.request().headers() };
    delete headers["x-radon-authless-test"];
    const response = await route.fetch({ headers });
    await route.fulfill({
      response,
      headers: { ...response.headers(), "access-control-allow-origin": "*" },
    });
  });
}

async function setupAmbientMocks(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await letClerkLoad(page);
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_EMPTY) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_EMPTY) }),
  );
  await page.route("**/api/prices", (route) => route.abort());
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
  );
}

async function setupMocks(page: Page, payload: Record<string, unknown> = IV_SPREAD_MOCK) {
  await setupAmbientMocks(page);
  await page.route("**/api/iv-spread", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) }),
  );
}

// `letClerkLoad` proxies the Clerk assets with a real `route.fetch`. When the
// test body finishes while one of those is still in flight, Playwright closes
// the page out from under the handler and the run fails on a route callback
// that has nothing to do with the assertions. Draining the routes before
// teardown is the documented remedy and the one the error text names.
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test.describe("/regime/iv-spread - NDX vs SPX 1M IV spread tab", () => {
  test("activates the IV SPREAD tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/iv-spread");

    // aria-current is the contract for "this is the open tab"; the `active`
    // class is how the stylesheet draws it. T-234.
    await expect(page.locator('[data-tab="iv-spread"]')).toHaveAttribute("aria-current", "page");

    const spread = page.locator('[data-testid="iv-spread-spread-value"]');
    await spread.waitFor({ timeout: 15_000 });
    await expect(spread).toHaveText("5.48");

    await expect(page.locator('[data-testid="iv-spread-regime-value"]')).toHaveText("NORMAL");
    await expect(page.locator('[data-testid="iv-spread-strip-ndx"]')).toContainText("17.6%");
    await expect(page.locator('[data-testid="iv-spread-strip-spx"]')).toContainText("12.1%");
    await expect(page.locator('[data-testid="iv-spread-strip-z"]')).toContainText("+0.10");
    await expect(page.locator('[data-testid="iv-spread-strip-pctile"]')).toContainText("59.4%");
    await expect(page.locator('[data-testid="iv-spread-strip-asof"]')).toContainText(LAST.date);
  });

  test("the freshness rail counts down to the IV SPREAD slot, to the minute", async ({ page }) => {
    // A rail counting down to the WRONG slot - the page reading a schedule
    // constant that is not IV_SPREAD_REFRESH - satisfied a shape check, and so
    // did a hardcoded `data-state`. Pin the clock and assert the figures
    // web/tests/freshness-rail.test.ts derives for this instant. T-221.
    await page.clock.setFixedTime(new Date(RAIL_NOW));
    await setupMocks(page, RAIL_PAYLOAD);
    await page.goto("/regime/iv-spread");

    const rail = page.getByTestId("iv-spread-freshness-rail");
    await rail.waitFor({ timeout: 15_000 });

    // The date the panel holds, and the countdown to the run that replaces it.
    await expect(rail).toContainText(RAIL_AS_OF);
    await expect(rail).toContainText("Next sample");

    // The clock starts in an effect, so the countdown resolves after hydration
    // rather than shipping a server-rendered time that would mismatch.
    const countdown = page.getByTestId("iv-spread-freshness-rail-countdown");
    await expect(countdown).not.toHaveText("--", { timeout: 15_000 });

    // radon-iv-spread.timer fires 22:15 UTC. 21:00 UTC is 1h15m short of it,
    // and any other schedule constant lands on a different number.
    await expect(countdown).toHaveText("1h 15m");

    // 17:00 ET Wednesday: Wednesday's close has printed and the panel holds
    // Tuesday, so the rail is BEHIND - not "current", and not yet "overdue",
    // because the run that owes the session has not fired.
    await expect(rail).toHaveAttribute("data-state", "behind");
    await expect(rail).toContainText("Awaiting 2026-08-26");

    // 22h45m of the 24h interval has elapsed since the 2026-08-25 22:15 run.
    await expect(page.getByTestId("iv-spread-freshness-rail-fill")).toHaveAttribute(
      "data-fill-pct",
      "94.79",
    );
  });

  test("renders the chart with real paths and a visible brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/iv-spread");

    const section = page.locator('[data-testid="iv-spread-chart-section"]');
    await section.waitFor({ timeout: 15_000 });
    await expect(section).toContainText("NDX VS SPX 1M ATM IMPLIED VOL SPREAD");

    // Two traces: the spread pane and the SPX 1M IV pane.
    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);

    await expect(section.locator('[data-testid="iv-spread-range-chips"]')).toBeVisible();
    await expect(section.locator('[data-testid="iv-spread-brush"]')).toBeVisible();

    // The excluded session's null spread is a gap, never a NaN path command.
    const ds = await section.locator("svg path").evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("d") ?? ""),
    );
    for (const d of ds) expect(d).not.toContain("NaN");
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, MISSING_IV_SPREAD);
    await page.goto("/regime/iv-spread");

    await expect(page.getByText("No NDX vs SPX IV spread data yet")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/iv-spread refresh timer/i)).toBeVisible();
  });
});
