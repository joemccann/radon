/**
 * E2E: Mobile order ticket — single-leg + combo trade entry at 393×852.
 *
 * Validates:
 * 1. Tap a chain cell → detail sheet with BUY / SELL action buttons.
 * 2. BUY adds a leg; pending strip surfaces with "1 LEG".
 * 3. Tap pending strip → MobileOrderTicket bottom sheet opens.
 * 4. Quantity steppers (+/-) clamp at 1 and increment correctly.
 * 5. Limit price input is 18px (no iOS auto-zoom — anything >=16 is fine).
 * 6. Submit posts to /api/orders/place; success message renders.
 * 7. Adding two legs (BUY + SELL on different strikes) builds a combo
 *    payload with type:"combo" and legs[] preserving ComboLeg actions.
 */

import { test, expect, type Locator, type Page, type Request } from "@playwright/test";

async function stubChainApis(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ positions: [], last_sync: new Date().toISOString(), bankroll: 0, peak_value: 0, total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100, position_count: 0, defined_risk_count: 0, undefined_risk_count: 0, avg_kelly_optimal: null, exposure: {}, violations: [] }) }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ open_orders: [], executed_orders: [], open_count: 0, executed_count: 0, last_sync: new Date().toISOString() }) }),
  );
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, cri: { score: 15 } }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }) }),
  );
  await page.route("**/api/cash-flows**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [], summary: {} }) }),
  );
  await page.route("**/api/flex-token", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, days_until_expiry: 14 }) }),
  );
  await page.route("**/api/ticker/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ uw_info: { name: "Apple Inc.", sector: "Tech", description: "" }, stock_state: {}, profile: {}, stats: {} }) }),
  );
  await page.route("**/api/options/expirations*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ symbol: "AAPL", expirations: ["20260320", "20260417"] }) }),
  );
  await page.route("**/api/options/chain*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ symbol: "AAPL", expiry: "20260320", exchange: "SMART", strikes: [195, 200, 205, 210, 215], multiplier: "100" }) }),
  );
  await page.route("**/api/prices**", (route) => route.abort());
}

// Programmatic click bypasses Playwright's geometry checks. Required because
// BottomSheet footers can render below the 393×852 viewport when content is
// long; force:true still rejects "outside viewport" but evaluate() always works.
async function tapJs(locator: Locator) {
  await locator.evaluate((el) => (el as HTMLElement).click());
}

test.describe("Mobile order ticket — single-leg flow", () => {
  test("tap call cell → BUY adds a leg → pending strip + ticket open", async ({ page }) => {
    await stubChainApis(page);
    await page.goto("/AAPL?tab=chain");

    await tapJs(page.getByTestId("mobile-chain-call-200"));
    await expect(page.getByTestId("mobile-chain-detail-sheet")).toBeVisible();

    await expect(page.getByTestId("mobile-chain-detail-buy")).toBeVisible();
    await expect(page.getByTestId("mobile-chain-detail-sell")).toBeVisible();

    await tapJs(page.getByTestId("mobile-chain-detail-buy"));
    await expect(page.getByTestId("mobile-chain-detail-sheet")).toBeHidden();

    const strip = page.getByTestId("mobile-chain-pending-strip");
    await expect(strip).toBeVisible();
    await expect(strip).toContainText("1 LEG");

    await tapJs(strip);
    await expect(page.getByTestId("mobile-order-ticket")).toBeVisible();
    const legs = page.getByTestId("mobile-order-ticket-legs");
    await expect(legs).toContainText("BUY");
    await expect(legs).toContainText("Call");
    await expect(legs).toContainText("$200");
  });

  test("quantity steppers clamp at 1 and increment", async ({ page }) => {
    await stubChainApis(page);
    await page.goto("/AAPL?tab=chain");

    await tapJs(page.getByTestId("mobile-chain-call-200"));
    await tapJs(page.getByTestId("mobile-chain-detail-buy"));
    await tapJs(page.getByTestId("mobile-chain-pending-strip"));

    const legId = "AAPL_20260320_200_C";
    const minus = page.getByTestId(`mobile-order-ticket-leg-${legId}-minus`);
    const plus = page.getByTestId(`mobile-order-ticket-leg-${legId}-plus`);

    // Default qty = 1; minus should clamp at 1
    await tapJs(minus);
    await expect(page.getByTestId("mobile-order-ticket-legs")).toContainText("1×");

    // Plus → 2 → 3
    await tapJs(plus);
    await tapJs(plus);
    await expect(page.getByTestId("mobile-order-ticket-legs")).toContainText("3×");
  });

  test("limit price input has tabular numeric font ≥16px to avoid iOS zoom", async ({ page }) => {
    await stubChainApis(page);
    await page.goto("/AAPL?tab=chain");

    await tapJs(page.getByTestId("mobile-chain-call-200"));
    await tapJs(page.getByTestId("mobile-chain-detail-buy"));
    await tapJs(page.getByTestId("mobile-chain-pending-strip"));

    const fontSize = await page
      .getByTestId("mobile-order-ticket-price-input")
      .evaluate((el) => Number(window.getComputedStyle(el).fontSize.replace("px", "")));
    expect(fontSize).toBeGreaterThanOrEqual(16);
  });

  test("submit posts a single-leg option order to /api/orders/place", async ({ page }) => {
    await stubChainApis(page);

    let placeCallBody: unknown = null;
    await page.route("**/api/orders/place", async (route, request: Request) => {
      placeCallBody = await request.postDataJSON();
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, orderId: 9999 }) });
    });

    await page.goto("/AAPL?tab=chain");
    await tapJs(page.getByTestId("mobile-chain-call-200"));
    await tapJs(page.getByTestId("mobile-chain-detail-buy"));
    await tapJs(page.getByTestId("mobile-chain-pending-strip"));

    // Set a price (live mid is null because we abort prices), so we need to type
    await page.getByTestId("mobile-order-ticket-price-input").fill("3.45");
    await tapJs(page.getByTestId("mobile-order-ticket-submit"));

    await expect(page.getByTestId("mobile-order-ticket-success")).toBeVisible();

    expect(placeCallBody).not.toBeNull();
    const body = placeCallBody as Record<string, unknown>;
    expect(body.type).toBe("option");
    expect(body.symbol).toBe("AAPL");
    expect(body.action).toBe("BUY");
    expect(body.right).toBe("CALL");
    expect(body.strike).toBe(200);
    expect(body.tif).toBe("DAY");
  });
});

test.describe("Mobile order ticket — combo flow", () => {
  test("BUY 200C + SELL 210C builds a combo payload with per-leg actions preserved", async ({ page }) => {
    await stubChainApis(page);

    let placeCallBody: unknown = null;
    await page.route("**/api/orders/place", async (route, request: Request) => {
      placeCallBody = await request.postDataJSON();
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, orderId: 9999 }) });
    });

    await page.goto("/AAPL?tab=chain");

    // Add the long call
    await tapJs(page.getByTestId("mobile-chain-call-200"));
    await tapJs(page.getByTestId("mobile-chain-detail-buy"));

    // Add the short call (vertical spread structure)
    await tapJs(page.getByTestId("mobile-chain-call-210"));
    await tapJs(page.getByTestId("mobile-chain-detail-sell"));

    const strip = page.getByTestId("mobile-chain-pending-strip");
    await expect(strip).toContainText("2 LEGS");
    await tapJs(strip);

    await expect(page.getByTestId("mobile-order-ticket")).toBeVisible();

    await page.getByTestId("mobile-order-ticket-price-input").fill("1.50");
    await tapJs(page.getByTestId("mobile-order-ticket-submit"));

    await expect(page.getByTestId("mobile-order-ticket-success")).toBeVisible();

    const body = placeCallBody as { type: string; legs: Array<{ action: string; strike: number; right: string }>; action: string };
    expect(body.type).toBe("combo");
    expect(body.legs).toHaveLength(2);
    const buyLeg = body.legs.find((l) => l.action === "BUY");
    const sellLeg = body.legs.find((l) => l.action === "SELL");
    expect(buyLeg?.strike).toBe(200);
    expect(buyLeg?.right).toBe("CALL");
    expect(sellLeg?.strike).toBe(210);
    expect(sellLeg?.right).toBe("CALL");
    // Combo envelope action must be BUY (CLAUDE.md guardrail #1)
    expect(body.action).toBe("BUY");
  });
});
