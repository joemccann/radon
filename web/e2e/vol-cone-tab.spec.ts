import { test, expect } from "@playwright/test";

function point(date: string, atm: number) {
  return {
    date,
    spot: 220,
    atm_iv: atm,
    call_10_iv: atm - 0.005,
    put_10_iv: atm + 0.01,
  };
}

function name(overrides: Record<string, unknown> = {}) {
  const series = Array.from({ length: 18 }, (_, i) =>
    point(`2026-04-${String(10 + i).padStart(2, "0")}`, 0.40 - i * 0.001),
  );
  return {
    ticker: "NVDA",
    spot: 223.95,
    expiry: "2026-09-18",
    dte: 37,
    atm_iv: 0.3851329156797111,
    call_10_iv: 0.3862120615005326,
    put_10_iv: 0.39731998999142565,
    call_10_strike: 246.345,
    put_10_strike: 201.555,
    p10: 0.3879,
    p90: 0.443,
    atm_percentile: 0,
    call_10_percentile: 0.0556,
    put_10_percentile: 0.1111,
    wing_score: 0.0833,
    regime: "CHEAP_WINGS",
    series,
    ...overrides,
  };
}

const NVDA = name();
const SMH = name({
  ticker: "SMH",
  regime: "NEUTRAL",
  wing_score: 0.44,
  atm_percentile: 0.4,
  atm_iv: 0.387,
});

const VOL_CONE_MOCK = {
  scan_time: "2026-08-12T20:45:00Z",
  source_as_of: "2026-08-12",
  count: 2,
  hit_count: 1,
  current: NVDA,
  names: [NVDA, SMH],
  hits: [NVDA],
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
  volConePayload: Record<string, unknown> = VOL_CONE_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/vol-cone", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(volConePayload) }),
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

test.describe("/scanner?mode=vol-cone — cheap 10% OTM wing IV scanner", () => {
  test("activates the VOL CONE scanner tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/scanner?mode=vol-cone");

    await expect(page.getByRole("tab", { name: "VOL CONE" })).toHaveAttribute("aria-selected", "true");

    const regime = page.locator('[data-testid="vol-cone-regime-value"]');
    await regime.waitFor({ timeout: 10_000 });
    await expect(regime).toHaveText("CHEAP WINGS");

    await expect(page.locator('[data-testid="vol-cone-strip-ticker"]')).toContainText("NVDA");
    await expect(page.locator('[data-testid="vol-cone-strip-atm"]')).toContainText("38.5");
    await expect(page.locator('[data-testid="vol-cone-strip-source"]')).toContainText("2026-08-12");
  });

  test("legacy /regime/vol-cone redirects to the scanner tab", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/vol-cone");
    await expect(page).toHaveURL(/\/scanner\?mode=vol-cone/);
    await expect(page.getByRole("tab", { name: "VOL CONE" })).toHaveAttribute("aria-selected", "true");
  });

  test("renders the table, cone chart, and brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/scanner?mode=vol-cone");

    const section = page.locator('[data-testid="vol-cone-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section).toContainText("NVDA 2026-09-18 90/10 VOL CONE");
    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    await expect(section.locator('[data-testid="vol-cone-brush"]')).toBeVisible();

    const table = page.locator('[data-testid="vol-cone-table-section"]');
    await expect(table.getByRole("button", { name: "ALL" })).toBeVisible();
    await expect(table.getByRole("button", { name: "HITS" })).toBeVisible();
    await expect(table).toContainText("SMH");
    await table.getByRole("button", { name: "HITS" }).click();
    await expect(table).not.toContainText("SMH");
    await expect(table).toContainText("NVDA");
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, {
      missing: true,
      scan_time: null,
      source_as_of: null,
      count: 0,
      hit_count: 0,
      current: null,
      names: [],
      hits: [],
    });
    await page.goto("/scanner?mode=vol-cone");

    await expect(page.getByText("No vol cone data yet")).toBeVisible({ timeout: 10_000 });
  });
});
