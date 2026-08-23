import { test, expect } from "@playwright/test";

const SESSION_SLOTS_UTC = [14, 15, 16, 17, 18, 19, 19];

function buildHourly() {
  const bars = [];
  for (let i = 0; i < 70; i++) {
    const day = Math.floor(i / 7);
    const slot = i % 7;
    const stamp = new Date(Date.UTC(2026, 6, 6 + day, SESSION_SLOTS_UTC[slot], slot === 6 ? 55 : 25));
    bars.push({
      ts: stamp.toISOString(),
      bucket: `${stamp.toISOString().slice(0, 10)}T${String(9 + slot).padStart(2, "0")}:30`,
      trin: Number((0.8 + 0.3 * Math.sin(i / 3)).toFixed(4)),
      ma10: i >= 9 ? Number((0.85 + 0.1 * Math.cos(i / 5)).toFixed(4)) : null,
    });
  }
  return bars;
}

const HOURLY = buildHourly();

const TRIN_MOCK = {
  scan_time: new Date().toISOString(),
  source: "ib+stockcharts",
  current: {
    ts: HOURLY[HOURLY.length - 1].ts,
    session_date: "2026-08-21",
    trin: 0.68,
    ma10: 0.61,
    state: "near_zone",
    adv: 1510,
    dec: 1280,
    up_vol: 1.9e9,
    down_vol: 1.4e9,
    daily_close: 0.68,
    daily_date: "2026-08-21",
    zone_low: 0.6,
    zone_near: 0.65,
    zone_high: 1.5,
  },
  hourly: HOURLY,
  daily: [
    { date: "2026-08-20", close: 0.77 },
    { date: "2026-08-21", close: 0.68 },
  ],
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
  payload: Record<string, unknown> = TRIN_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/trin", (route) =>
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

test.describe("/regime/trin - TRIN 60-minute tab", () => {
  test("activates the TRIN tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/trin");

    await expect(page.locator('.regime-rail__item[data-tab="trin"]')).toHaveClass(/active/);

    const value = page.locator('[data-testid="trin-value"]');
    await value.waitFor({ timeout: 10_000 });
    await expect(value).toHaveText("0.68");
    await expect(page.locator('[data-testid="trin-ma10"]')).toHaveText("0.61");
    await expect(page.locator('[data-testid="trin-state"]')).toHaveText("NEAR ZONE");
    await expect(page.locator('[data-testid="trin-daily"]')).toHaveText("0.68");
    await expect(page.locator('[data-testid="trin-advdec"]')).toHaveText("1510 / 1280");
    await expect(page.locator('[data-testid="trin-source"]')).toContainText("IB+STOCKCHARTS");
  });

  test("renders the TRIN and MA10 series with the brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/trin");

    const section = page.locator('[data-testid="trin-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    expect(await section.locator("svg path[stroke]").count()).toBeGreaterThanOrEqual(2);

    await expect(section).toContainText("TRIN 60 MIN");
    await expect(section.locator('[data-testid="trin-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, {
      missing: true,
      scan_time: null,
      source: null,
      current: null,
      hourly: [],
      daily: [],
    });

    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/trin") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/trin");

    await expect(page.getByText("No TRIN samples yet")).toBeVisible({ timeout: 10_000 });
    expect(failedApiResponses).toEqual([]);
  });
});
