import { test, expect, type Page } from "@playwright/test";

// Spec: docs/indicators/ivrank.md section H.5.
//
// Pins the /regime/ivrank contract against a payload this file owns: tab
// activation, the summary strip, the chart with its brush, and the missing
// state. Ambient routes (portfolio, orders, ib-status) are mocked and the
// prices stream aborted so the workspace shell settles without a backend.
//
// Not in CI. Run locally:
//   cd web && PLAYWRIGHT_PORT=3033 RADON_AUTHLESS_TEST=1 npx playwright test ivrank-tab

// ~320 sessions ending 2026-08-21. The first 60 rows predate a full rank
// window so their iv_rank / iv_pct are null: the chart must gap, never zero.
function buildSeries() {
  const series: Array<{
    date: string;
    iv: number;
    iv_rank: number | null;
    iv_pct: number | null;
  }> = [];
  const day = new Date(Date.UTC(2025, 4, 12));
  let i = 0;
  while (series.length < 320) {
    const dow = day.getUTCDay();
    if (dow !== 0 && dow !== 6) {
      const preWindow = i < 60;
      series.push({
        date: day.toISOString().slice(0, 10),
        iv: 0.11 + (i % 17) * 0.004,
        iv_rank: preWindow ? null : 8 + (i % 60),
        iv_pct: preWindow ? null : 10 + (i % 55),
      });
      i += 1;
    }
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return series;
}

const SERIES = buildSeries();
const LAST = SERIES[SERIES.length - 1];

const IVRANK_MOCK = {
  scan_time: new Date().toISOString(),
  status: "ok",
  source: "ib",
  as_of: LAST.date,
  expected_session: LAST.date,
  market_status: "closed",
  rank_window: 252,
  count: SERIES.length,
  rank_count: SERIES.filter((entry) => entry.iv_rank != null).length,
  current: {
    date: LAST.date,
    iv: 0.12201147,
    iv_rank: 10.559822,
    iv_pct: 11.952191,
    iv_1y_low: 0.10542261,
    iv_1y_high: 0.26251674,
    rank_change_1d: -1.2,
    regime: "SUPPRESSED",
  },
  uw_check: { date: LAST.date, iv_rank: 10.58 },
  stats: {
    min: 0,
    p25: 18.4,
    median: 41.2,
    p75: 66,
    max: 100,
    mean: 43.1,
    share_suppressed: 0.24,
    share_extreme: 0.06,
  },
  series: SERIES,
};

const MISSING_IVRANK = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  count: 0,
  series: [],
  current: null,
  stats: null,
  uw_check: null,
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
// the preflight with its version-pin 307 — which is not allowed for a
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

async function setupMocks(page: Page, payload: Record<string, unknown> = IVRANK_MOCK) {
  await setupAmbientMocks(page);
  await page.route("**/api/ivrank", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) }),
  );
}

test.describe("/regime/ivrank — SPY 1M IV rank tab", () => {
  test("activates the IV RANK tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/ivrank");

    await expect(page.locator('.regime-rail__item[data-tab="ivrank"]')).toHaveClass(/active/);

    const rank = page.locator('[data-testid="ivrank-rank-value"]');
    await rank.waitFor({ timeout: 15_000 });
    await expect(rank).toHaveText("10.6");

    await expect(page.locator('[data-testid="ivrank-regime-value"]')).toHaveText("SUPPRESSED");
    await expect(page.locator('[data-testid="ivrank-strip-iv"]')).toContainText("12.2%");
    await expect(page.locator('[data-testid="ivrank-strip-range"]')).toContainText("10.5% - 26.3%");
    await expect(page.locator('[data-testid="ivrank-strip-asof"]')).toContainText(LAST.date);
    await expect(page.locator('[data-testid="ivrank-uw-check"]')).toContainText("UW CROSS-CHECK 10.6");
  });

  test("the freshness rail counts down to the next scheduled sample", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/ivrank");

    const rail = page.locator('[data-testid="ivrank-freshness-rail"]');
    await rail.waitFor({ timeout: 15_000 });

    // The date the panel holds, and the countdown to the run that replaces it.
    await expect(rail).toContainText(LAST.date);
    await expect(rail).toContainText("Next sample");

    // The clock starts in an effect, so the countdown resolves after hydration
    // rather than shipping a server-rendered time that would mismatch.
    const countdown = page.locator('[data-testid="ivrank-freshness-rail-countdown"]');
    await expect(countdown).not.toHaveText("--", { timeout: 15_000 });
    await expect(countdown).toHaveText(/^(\d+h \d{2}m|\d+m \d{2}s|\d+s|Due)$/);

    // Whatever the state, it is one of the four the rail models.
    await expect(rail).toHaveAttribute("data-state", /^(current|behind|overdue)$/);

    // The track is drawn, and its fill never exceeds the interval.
    const fillWidth = await rail.locator(".freshness-rail-track-fill").evaluate(
      (node) => (node as HTMLElement).style.width,
    );
    expect(Number.parseFloat(fillWidth)).toBeGreaterThanOrEqual(0);
    expect(Number.parseFloat(fillWidth)).toBeLessThanOrEqual(100);
  });

  test("renders the chart with real paths and a visible brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/ivrank");

    const section = page.locator('[data-testid="ivrank-chart-section"]');
    await section.waitFor({ timeout: 15_000 });
    await expect(section).toContainText("SPY 1M IV RANK");

    // Two traces: the 1M IV pane and the rank pane.
    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);

    await expect(section.locator('[data-testid="ivrank-range-chips"]')).toBeVisible();
    await expect(section.locator('[data-testid="ivrank-brush"]')).toBeVisible();

    // Pre-window null ranks are gaps, never NaN path commands.
    const ds = await section.locator("svg path").evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("d") ?? ""),
    );
    for (const d of ds) expect(d).not.toContain("NaN");
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, MISSING_IVRANK);
    await page.goto("/regime/ivrank");

    await expect(page.getByText("No SPY IV rank data yet")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/ivrank refresh timer/i)).toBeVisible();
  });
});
