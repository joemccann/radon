import { test, expect } from "@playwright/test";
import { mkdirSync } from "fs";
import { dirname, resolve } from "path";
import { fileURLToPath } from "url";

const SERIES_LENGTH = 600;
const DATA_DATE = "2026-09-17";

function daysAgo(n: number): string {
  const d = new Date(`${DATA_DATE}T20:00:00Z`);
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

function buildSeries() {
  const points = [];
  for (let i = 0; i < SERIES_LENGTH; i++) {
    points.push({
      date: daysAgo(SERIES_LENGTH - 1 - i),
      vix: 15 + 2 * Math.sin(i / 20),
      vix3m: 18 + Math.sin(i / 30),
      vvix: 90 + 5 * Math.sin(i / 25),
      ts: 0.83 + 0.05 * Math.sin(i / 22),
      skew: 145 + 2 * Math.sin(i / 18),
      z_vix: i < 251 ? null : 0.1 * Math.sin(i / 20),
      z_vvix: i < 251 ? null : 0.1 * Math.sin(i / 21),
      z_ts: i < 251 ? null : 0.1 * Math.sin(i / 19),
      z_skew: i < 251 ? null : 0.1 * Math.sin(i / 17),
      level: i < 251 ? null : 0.2 * Math.sin(i / 20),
      delta_1d: i < 252 ? null : 0.05 * Math.sin(i / 20),
    });
  }
  return points;
}

const PANIC_MOCK = {
  scan_time: "2026-09-18T02:50:00Z",
  source_last_modified: {
    vix: "Fri, 18 Sep 2026 01:51:00 GMT",
    vix3m: "Fri, 18 Sep 2026 01:51:00 GMT",
    vvix: "Fri, 18 Sep 2026 12:01:00 GMT",
    skew: "Fri, 18 Sep 2026 21:01:00 GMT",
  },
  data_date: DATA_DATE,
  count: SERIES_LENGTH,
  delta_count: SERIES_LENGTH - 252,
  current: {
    date: DATA_DATE,
    level: -0.6256,
    delta_1d: -0.6269,
    delta_z: -1.75,
    delta_std_10y: 0.3574,
    rank_decline_10y: 87,
    rank_surge_10y: 2423,
    rank_n: 2509,
    legs: {
      vix: { value: 15.44, z: -0.8252 },
      vvix: { value: 87.72, z: -1.0661 },
      ts: { value: 0.8323, z: -0.7683, vix3m: 18.55 },
      skew: { value: 145.7, z: 0.1573 },
      skew25d: null,
    },
  },
  stats: {
    high: 3.7143,
    high_date: "2018-02-05",
    low: -2.3411,
    low_date: "2024-08-06",
    avg: -0.0007,
    stddev: 0.3574,
  },
  alert: { last_fired_date: null, last_fired_kind: null },
  series: buildSeries(),
};

const MISSING_PANIC = {
  missing: true,
  scan_time: null,
  source_last_modified: null,
  data_date: null,
  count: 0,
  delta_count: 0,
  current: null,
  stats: null,
  alert: null,
  series: [],
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

async function setupMocks(
  page: import("@playwright/test").Page,
  payload: Record<string, unknown> = PANIC_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/panic-index", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) }),
  );
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

test.describe("/regime/panic-index - Panic Proxy tab", () => {
  test("activates the PANIC tab and shows the disclaimer", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/panic-index");

    await expect(page.locator('.regime-rail__item[data-tab="panic-index"]')).toHaveClass(/active/);
    await expect(page.getByTestId("panic-index-disclaimer")).toHaveText(
      "Radon reconstruction - not the Goldman Sachs index",
    );
    await expect(page.getByTestId("panic-index-delta")).toHaveText("-0.63");
    await expect(page.getByTestId("panic-index-level")).toHaveText("-0.63");
    await expect(page.getByTestId("panic-index-rank")).toHaveText("#87 of 2509");
  });

  test("renders stroked paths, brush, and the CHANGE title", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/panic-index");

    const section = page.locator('[data-testid="panic-index-chart-section"]');
    await section.waitFor({ timeout: 10_000 });
    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    await expect(section).toContainText("PANIC PROXY - 1D CHANGE");
    await expect(section.locator('[data-testid="panic-index-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, MISSING_PANIC);
    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/panic-index") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });
    await page.goto("/regime/panic-index");
    await expect(page.getByText("No panic proxy reading yet")).toBeVisible({ timeout: 10_000 });
    expect(failedApiResponses).toEqual([]);
  });

  test("captures the checked-in tab screenshot with the disclaimer", async ({ page }) => {
    await setupMocks(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/regime/panic-index");
    await expect(page.getByTestId("panic-index-disclaimer")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('[data-testid="panic-index-chart-section"] svg path[stroke]').first()).toBeVisible();
    const dest = resolve(
      dirname(fileURLToPath(import.meta.url)),
      "../../docs/indicators/panic-index-tab.png",
    );
    mkdirSync(dirname(dest), { recursive: true });
    await page.screenshot({ path: dest, fullPage: false });
  });
});
