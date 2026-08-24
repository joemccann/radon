import { test, expect } from "@playwright/test";

const SERIES_LENGTH = 320;

// Window-relative quarter starts: the series always ends on the last
// completed quarter, never a hardcoded date.
function quarterStart(quartersBack: number): string {
  const now = new Date();
  const total = now.getUTCFullYear() * 4 + Math.floor(now.getUTCMonth() / 3) - quartersBack;
  const year = Math.floor(total / 4);
  const month = (total % 4) * 3 + 1;
  return `${year}-${String(month).padStart(2, "0")}-01`;
}

const DATA_DATE = quarterStart(1);
const DATA_QUARTER_LABEL = (() => {
  const [year, month] = DATA_DATE.split("-").map(Number);
  return `${year} Q${Math.floor((month - 1) / 3) + 1}`;
})();

function buildSeries() {
  const points = [];
  for (let i = 0; i < SERIES_LENGTH; i++) {
    points.push({
      date: i === SERIES_LENGTH - 1 ? DATA_DATE : quarterStart(SERIES_LENGTH - i),
      // Smooth, inside the plausible 2..40 band, never NaN.
      leverage_pct: 12 + 8 * Math.sin(i / 16),
    });
  }
  points[SERIES_LENGTH - 1].leverage_pct = 11.78;
  return points;
}

// Mirrors the spec payload: 11.78 percent leverage is DELEVERAGED.
const HHLEV_MOCK = {
  scan_time: new Date().toISOString(),
  source_last_modified: quarterStart(1),
  data_date: DATA_DATE,
  current: {
    date: DATA_DATE,
    leverage_pct: 11.78,
    liabilities_musd: 21560050,
    net_worth_musd: 182979889,
  },
  series: buildSeries(),
};

const MISSING_HHLEV = {
  missing: true,
  scan_time: null,
  source_last_modified: null,
  data_date: null,
  current: null,
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
  payload: Record<string, unknown> = HHLEV_MOCK,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/hhlev", (route) =>
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

test.describe("/regime/hhlev - household leverage tab", () => {
  test("activates the HH LEV tab and renders the summary strip", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/hhlev");

    await expect(page.locator('.regime-rail__item[data-tab="hhlev"]')).toHaveClass(/active/);

    const leverage = page.locator('[data-testid="hhlev-leverage"]');
    await leverage.waitFor({ timeout: 10_000 });
    await expect(leverage).toHaveText("11.78%");
    await expect(page.locator('[data-testid="hhlev-liab"]')).toHaveText("$21.6T");
    await expect(page.locator('[data-testid="hhlev-networth"]')).toHaveText("$183.0T");
    await expect(page.locator('[data-testid="hhlev-regime"]')).toHaveText("DELEVERAGED");
    await expect(page.locator('[data-testid="hhlev-updated"]')).toHaveText(DATA_QUARTER_LABEL);
  });

  test("renders the leverage series with the range chips and brush", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/regime/hhlev");

    const section = page.locator('[data-testid="hhlev-chart-section"]');
    await section.waitFor({ timeout: 10_000 });

    await expect(section.locator("svg path[stroke]").first()).toBeVisible();
    await expect(section).toContainText("US HOUSEHOLD LEVERAGE PCT OF NET WORTH");
    await expect(section.locator('[data-testid="hhlev-range-chips-all"]')).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(section.locator('[data-testid="hhlev-brush"]')).toBeVisible();
  });

  test("shows the empty state on missing:true without a 4xx", async ({ page }) => {
    await setupMocks(page, MISSING_HHLEV);

    const failedApiResponses: string[] = [];
    page.on("response", (res) => {
      if (res.url().includes("/api/hhlev") && res.status() >= 400) {
        failedApiResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    await page.goto("/regime/hhlev");

    await expect(page.getByText("No household leverage data yet")).toBeVisible({ timeout: 10_000 });
    expect(failedApiResponses).toEqual([]);
  });
});
