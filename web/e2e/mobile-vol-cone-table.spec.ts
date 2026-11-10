/**
 * E2E: vol cone scanner table on the mobile shell (393×852).
 *
 * Bug (2026-08-18 screenshot): the Names table was unreadable on a phone —
 * cell text bisected by the panel's right border, the whole page scrolling
 * sideways, the Ticker column lost off-screen. The table's wrapper class
 * (`table-scroll`) was defined in no stylesheet, so the 9-column table
 * overflowed the document instead of scrolling inside its container.
 *
 * Invariant pinned here: the page body never scrolls horizontally; the wide
 * table scrolls inside its own overflow-x wrapper.
 */

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

async function setupMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/vol-cone", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(VOL_CONE_MOCK) }),
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

test.describe("vol cone scanner table — mobile shell", () => {
  test("the wide Names table scrolls in its wrapper, never the page", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/scanner?mode=vol-cone");

    const row = page.getByTestId("vol-cone-row-NVDA-2026-09-18");
    await expect(row).toBeVisible();

    // The page body must not scroll horizontally.
    const overflow = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(
      overflow.scrollWidth,
      `document scrolls sideways (${overflow.scrollWidth}px > ${overflow.innerWidth}px viewport)`,
    ).toBeLessThanOrEqual(overflow.innerWidth + 1);

    // The wide table's own wrapper is the scroll container.
    const wrapper = page
      .getByTestId("vol-cone-table-section")
      .locator(".table-wrap")
      .first();
    const wrapperState = await wrapper.evaluate((el) => ({
      overflowX: window.getComputedStyle(el).overflowX,
      clientWidth: el.clientWidth,
      scrollWidth: el.scrollWidth,
    }));
    expect(wrapperState.overflowX).toBe("auto");
    // 9 columns cannot fit 393px — the wrapper, not the page, absorbs it.
    expect(wrapperState.scrollWidth).toBeGreaterThan(wrapperState.clientWidth);
  });

  test("the Ticker column is visible at rest on a phone", async ({ page }) => {
    await setupMocks(page);
    await page.goto("/scanner?mode=vol-cone");

    const row = page.getByTestId("vol-cone-row-NVDA-2026-09-18");
    await expect(row).toBeVisible();

    // At scrollLeft 0 the first (Ticker) cell must be inside the viewport —
    // the screenshot showed expiry groups with no way to tell which ticker
    // they belonged to.
    const tickerCell = row.locator("td").first();
    await expect(tickerCell).toContainText("NVDA");
    const box = await tickerCell.boundingBox();
    expect(box, "ticker cell has no box").not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x).toBeLessThan(393);
  });
});
