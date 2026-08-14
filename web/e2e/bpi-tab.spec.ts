import { test, expect } from "@playwright/test";

/**
 * /regime/bpi AS OF SESSION stale mark.
 *
 * After 16:00 ET on a weekday the last completed session is today. A
 * payload still dated yesterday must keep the date and show STALE
 * (data-testid=bpi-session-stale). Clock is frozen to the 2026-08-13
 * 19:22 ET production screenshot; as_of is 2026-08-12.
 */

const AFTER_CLOSE = new Date("2026-08-13T19:22:00-04:00");

function bpiPayload(asOf: string, symbol: "NDX" | "SPX" | "RUT", members: number) {
  return {
    schema_version: 1,
    index_symbol: symbol,
    index_name: symbol === "NDX" ? "Nasdaq-100" : symbol === "SPX" ? "S&P 500" : "Russell 2000",
    taken_at: `${asOf}T21:30:00Z`,
    as_of_session: asOf,
    bpi: 42.5,
    members,
    bullish: 40,
    state: "NEUTRAL",
    cross_up_30: false,
    thresholds: { oversold: 30, overbought: 70 },
    history: [
      { date: "2026-08-11", bpi: 41.0 },
      { date: asOf, bpi: 42.5 },
    ],
  };
}

function bpiResponse(asOf: string) {
  return {
    generated_at: `${asOf}T21:30:00Z`,
    indices: {
      NDX: bpiPayload(asOf, "NDX", 100),
      SPX: bpiPayload(asOf, "SPX", 500),
      RUT: bpiPayload(asOf, "RUT", 1950),
    },
  };
}

const PORTFOLIO_EMPTY = {
  bankroll: 100_000,
  positions: [],
  account_summary: {},
  exposure: {},
  violations: [],
};

const ORDERS_EMPTY = {
  last_sync: "2026-08-13T23:22:00Z",
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

async function setupMocks(
  page: import("@playwright/test").Page,
  payload: Record<string, unknown>,
) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/bpi", (route) =>
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

test.describe("/regime/bpi — Bullish Percent Index tab", () => {
  test("AS OF SESSION shows yesterday and STALE after the 16:00 ET close", async ({ page }) => {
    await page.clock.install({ time: AFTER_CLOSE });
    await setupMocks(page, bpiResponse("2026-08-12"));
    await page.goto("/regime/bpi");

    await expect(page.locator(".ticker-tab", { hasText: "BULLISH %" })).toHaveClass(/active/);
    await page.getByTestId("bpi-index-chip-SPX").click();

    const cell = page.getByTestId("bpi-strip-session");
    await expect(cell).toContainText("2026-08-12");
    await expect(page.getByTestId("bpi-session-stale")).toHaveText(/STALE/);
    await expect(cell).not.toContainText(/Refreshes nightly/i);
  });

  test("AS OF SESSION is current after the close when as_of is today", async ({ page }) => {
    await page.clock.install({ time: AFTER_CLOSE });
    await setupMocks(page, bpiResponse("2026-08-13"));
    await page.goto("/regime/bpi");

    await expect(page.getByTestId("bpi-strip-session")).toContainText("2026-08-13");
    await expect(page.getByTestId("bpi-session-stale")).toHaveCount(0);
  });
});
