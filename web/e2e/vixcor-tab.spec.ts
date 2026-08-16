import { test, expect, type Page } from "@playwright/test";

// Spec: docs/indicators/vixcor.md section H.9.
//
// Two halves. The mocked half pins the contract (tab activation, both panes,
// episode shading, the open-episode marker, the brush, the base-rate block,
// the missing state) against a payload this file owns. The live half loads the
// real /api/vixcor off the dev server, proves the tab is not an empty state,
// and drops the theme screenshots the build's acceptance criteria call for.
//
// Not in CI. Run locally:
//   cd web && PLAYWRIGHT_PORT=3033 RADON_AUTHLESS_TEST=1 npx playwright test vixcor-tab

const SHOT_DIR =
  process.env.VIXCOR_SHOT_DIR ?? "/tmp/vixcor-after";

// ~320 joined sessions ending 2026-08-14. The tail decouples into an open
// episode so the live-shape assertions have something to bite on.
function buildSeries() {
  const series: Array<{
    date: string;
    vix_close: number;
    cor3m_close: number;
    corr20: number | null;
    episode: boolean;
  }> = [];
  const day = new Date(Date.UTC(2025, 4, 12));
  let i = 0;
  while (series.length < 320) {
    const dow = day.getUTCDay();
    if (dow !== 0 && dow !== 6) {
      const decoupled = i >= 300;
      const midEpisode = i >= 150 && i <= 158;
      series.push({
        date: day.toISOString().slice(0, 10),
        vix_close: 14 + (i % 13) * 0.4,
        cor3m_close: 11 + (i % 9) * 0.5,
        corr20:
          i < 19
            ? null
            : decoupled
              ? 0.24 - (i - 300) * 0.02
              : midEpisode
                ? 0.18
                : 0.72 + (i % 7) * 0.03,
        episode: decoupled || midEpisode,
      });
      i += 1;
    }
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return series;
}

const SERIES = buildSeries();
const LAST = SERIES[SERIES.length - 1];

const EPISODES = [
  {
    trigger: SERIES[150].date,
    start: SERIES[150].date,
    end: SERIES[158].date,
    sessions: 9,
    trough: 0.18,
    trough_date: SERIES[152].date,
    corr_at_trigger: 0.18,
    vix_at_trigger: 16.29,
    open: false,
    forward: { "5": 0.058, "10": 0.33, "21": 0.554, "42": 0.622, "63": null },
  },
  {
    trigger: SERIES[300].date,
    start: SERIES[300].date,
    end: LAST.date,
    sessions: 20,
    trough: LAST.corr20 ?? 0.01,
    trough_date: LAST.date,
    corr_at_trigger: 0.24,
    vix_at_trigger: 15.28,
    open: true,
    forward: { "5": null, "10": null, "21": null, "42": null, "63": null },
  },
];

function bucket(mean: number, n: number) {
  return { n, mean_drawup: mean, median_drawup: mean * 0.8, p_higher: 0.46, p_drawup_20: 0.15 };
}

const VIXCOR_MOCK = {
  scan_time: new Date().toISOString(),
  source_last_modified: { vix: "Sat, 15 Aug 2026 23:01:11 GMT" },
  status: "ok",
  as_of: LAST.date,
  parent_as_of: LAST.date,
  vix_as_of: LAST.date,
  expected_session: LAST.date,
  lag_sessions: 0,
  market_status: "closed",
  window: 20,
  count: SERIES.length,
  corr_count: SERIES.filter((entry) => entry.corr20 != null).length,
  current: {
    date: LAST.date,
    vix_close: LAST.vix_close,
    cor3m_close: LAST.cor3m_close,
    corr20: LAST.corr20,
    change_1d: -0.0148,
    percentile: 0.0186,
    regime: "DECOUPLED",
    vix_cov_20d: 0.1064,
    episode: EPISODES[1],
  },
  stats: {
    min: -0.5324,
    p01: -0.1135,
    p05: 0.2586,
    p10: 0.457,
    p25: 0.6795,
    median: 0.8446,
    p75: 0.925,
    p90: 0.9582,
    p95: 0.9723,
    p99: 0.9857,
    max: 0.9932,
    mean: 0.7621,
    stddev: 0.2341,
    share_below_zero: 0.0177,
    share_below_trigger: 0.0489,
    vix_cov_breakdown: 0.0691,
    vix_cov_coupled: 0.118,
  },
  episodes: EPISODES,
  forward_stats: {
    horizons: [5, 10, 21, 42, 63],
    event: {
      "5": bucket(0.0392, 30),
      "10": bucket(0.08, 30),
      "21": bucket(0.2089, 30),
      "42": bucket(0.3301, 30),
      "63": bucket(0.4392, 29),
    },
    base: {
      "5": bucket(0.0896, 5147),
      "10": bucket(0.1582, 5142),
      "21": bucket(0.2736, 5131),
      "42": bucket(0.4401, 5110),
      "63": bucket(0.5726, 5089),
    },
  },
  series: SERIES,
};

const MISSING_VIXCOR = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  count: 0,
  series: [],
  episodes: [],
  current: null,
  stats: null,
  forward_stats: null,
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
// preflight, so clerk-js never loads and the whole tree never hydrates. Refetch
// the Clerk assets without the header and hand them back with a permissive
// ACAO so the page hydrates and the panel's own fetch actually fires.
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

async function setupMocks(page: Page, payload: Record<string, unknown> = VIXCOR_MOCK) {
  await setupAmbientMocks(page);
  await page.route("**/api/vixcor", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) }),
  );
}

async function applyTheme(page: Page, theme: "dark" | "light") {
  await page.addInitScript((value) => {
    window.localStorage.setItem("theme", value as string);
  }, theme);
}

test.describe("/regime/vixcor — VIX vs COR3M correlation tab", () => {
  test("activates the VIX-COR tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/vixcor");

    await expect(page.locator(".ticker-tab", { hasText: "VIX-COR" })).toHaveClass(/active/);

    const regime = page.locator('[data-testid="vixcor-regime-value"]');
    await regime.waitFor({ timeout: 15_000 });
    await expect(regime).toHaveText("DECOUPLED");

    await expect(page.locator('[data-testid="vixcor-strip-vix"]')).toContainText(
      LAST.vix_close.toFixed(2),
    );
    await expect(page.locator('[data-testid="vixcor-strip-episode"]')).toContainText("SESSIONS");
    await expect(page.locator('[data-testid="vixcor-strip-asof"]')).toContainText(LAST.date);
    await expect(page.locator('[data-testid="vixcor-lag-copy"]')).not.toBeEmpty();
  });

  test("renders both panes, the episode shading and the open-episode marker", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/vixcor");

    const section = page.locator('[data-testid="vixcor-chart-section"]');
    await section.waitFor({ timeout: 15_000 });
    await expect(section).toContainText("VIX VS 3-MONTH IMPLIED CORRELATION");

    // Two traces: the VIX pane and the corr20 pane.
    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);

    await expect(section.locator('[data-testid="vixcor-zero-line"]')).toHaveCount(1);
    await expect(section.locator('[data-testid="vixcor-threshold-line"]')).toHaveCount(1);
    await expect(section.locator('[data-testid="vixcor-episode-shade"]')).toHaveCount(EPISODES.length);
    await expect(section.locator('[data-testid="vixcor-episode-open"]')).toHaveCount(1);

    await expect(section.locator('[data-testid="vixcor-range-chips"]')).toBeVisible();
    await expect(section.locator('[data-testid="vixcor-brush"]')).toBeVisible();

    // No arrow annotation may ever be drawn on this chart.
    await expect(section.locator("marker")).toHaveCount(0);
  });

  test("renders the base-rate block with both columns and no over-claim copy", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/vixcor");

    const block = page.locator('[data-testid="vixcor-base-rate"]');
    await block.waitFor({ timeout: 15_000 });
    await expect(block).toContainText("AFTER A BREAKDOWN");
    await expect(block).toContainText("ALL SESSIONS");
    await expect(block.locator('[data-testid="vixcor-base-rate-row-5"]')).toContainText("+3.9%");
    await expect(block.locator('[data-testid="vixcor-base-rate-row-5"]')).toContainText("+9.0%");

    // The ban is on the CLAIM, not on its negation — the sanctioned copy says
    // "not a warning" / "not a forecast", which is the point. Same convention
    // as web/tests/vixcor-panel.test.tsx.
    const rendered = await page.locator(".regime-panel").innerText();
    const scanned = rendered.replace(
      /\b(?:not|never)\s+(?:a|an)\s+(?:warning|forecast|prediction)\b/gi,
      "",
    );
    for (const banned of [
      /warning shot/i,
      /\bwarning\b/i,
      /\bpredicts?\b/i,
      /\bpredictive\b/i,
      /\bprediction\b/i,
      /signal ahead of/i,
      /\bforecasts?\b/i,
      /hit rate/i,
      /\b4[\s-](?:of|for)[\s-]4\b/i,
    ]) {
      expect(scanned).not.toMatch(banned);
    }
    expect(rendered).not.toContain("—");
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, MISSING_VIXCOR);
    await page.goto("/regime/vixcor");

    await expect(page.getByText("No VIX correlation data yet")).toBeVisible({ timeout: 15_000 });
  });

  for (const theme of ["dark", "light"] as const) {
    test(`live payload renders real data in the ${theme} theme`, async ({ page }) => {
      await applyTheme(page, theme);
      await setupAmbientMocks(page); // /api/vixcor deliberately NOT mocked.
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto("/regime/vixcor");

      const regime = page.locator('[data-testid="vixcor-regime-value"]');
      await regime.waitFor({ timeout: 30_000 });
      await expect(page.getByText("No VIX correlation data yet")).toHaveCount(0);

      const section = page.locator('[data-testid="vixcor-chart-section"]');
      await expect(section.locator("svg path[stroke]").first()).toBeVisible();
      // The live 2026-08 episode is open and unresolved.
      await expect(section.locator('[data-testid="vixcor-episode-open"]')).toHaveCount(1);
      await expect(page.locator('[data-testid="vixcor-base-rate"]')).toBeVisible();

      // No path may carry a NaN command.
      const ds = await section.locator("svg path").evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute("d") ?? ""),
      );
      for (const d of ds) expect(d).not.toContain("NaN");

      await page.screenshot({ path: `${SHOT_DIR}/vixcor-${theme}-1440x900.png` });

      // The workspace scrolls inside its own container, so a fullPage capture
      // never reaches the correlation pane or the base-rate block. Scroll to
      // them explicitly and capture the lower half.
      await page.locator('[data-testid="vixcor-base-rate"]').scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      await page.screenshot({ path: `${SHOT_DIR}/vixcor-${theme}-1440x900-lower.png` });

      await page.unrouteAll({ behavior: "ignoreErrors" });
    });
  }
});

test.describe("/regime/vixcor — mobile shell", () => {
  test.use({ viewport: { width: 393, height: 852 } });

  test("renders the live tab inside the mobile shell", async ({ page }) => {
    await setupAmbientMocks(page); // live /api/vixcor
    await page.goto("/regime/vixcor");

    await page.locator('[data-testid="vixcor-mobile-grid"]').waitFor({ timeout: 30_000 });
    await expect(page.getByText("No VIX correlation data yet")).toHaveCount(0);
    await expect(page.locator('[data-testid="vixcor-chart-section"]')).toBeVisible();

    await page.screenshot({ path: `${SHOT_DIR}/vixcor-mobile-393x852.png` });
    await page.locator('[data-testid="vixcor-chart-section"]').scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${SHOT_DIR}/vixcor-mobile-393x852-chart.png` });

    // Clerk keeps polling its own origin after the assertions land; leaving the
    // proxy route armed turns that in-flight request into a teardown failure.
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });
});
