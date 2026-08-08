/**
 * E2E: SPXU combo order entry — verifies that IB rejection states surface as
 * errors in the UI (red path) and that a valid placement shows the order (green path).
 *
 * All IB API calls are intercepted via page.route() — no live IB connection needed.
 *
 * Navigation contract (rewritten 2026-08-07 after the cockpit rebuild):
 * - Ticker detail is a ROUTE (app/[ticker]/page.tsx), not a modal; `TickerLink`
 *   (components/TickerLink.tsx) routes to `/<TICKER>?posId=<id>` via `useTickerNav`.
 * - There is no "Order" tab any more. `AssetCockpit`
 *   (components/ticker-detail/AssetCockpit.tsx:174-197) renders `OrderTab` in the
 *   always-visible desktop `.act-ticket` column; the glyph rail decks are
 *   chain/posn/news/rate/seas/info only (components/ticker-detail/GlyphRail.tsx).
 * - Error copy still routes through `formatOrderError`
 *   (web/lib/orderError.ts) into `.order-error` via `<OrderErrorBanner>`;
 *   success routes through `pushNotification` → the global `.toast-success`
 *   (components/ticker-detail/OrderTab.tsx:776, components/Toast.tsx:25).
 */

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Position id the cockpit deep link resolves (`?posId=`); matches PORTFOLIO_MOCK. */
const SPXU_POSITION_ID = 23;

/**
 * Open the SPXU cockpit and return the always-visible order ticket.
 *
 * Deep-links to the cockpit route rather than clicking the portfolio's
 * `TickerLink`. Both are real entry points, and the deep link is the pattern the
 * rest of this suite uses (e2e/iwm-close-order-summary.spec.ts:277), but they are
 * NOT equivalent today: the client-side `router.push` path leaves the cockpit
 * with `portfolio === null` indefinitely, because `TickerWorkspace` reads the
 * snapshot through `TickerDetailContext`'s ref getter (lib/TickerDetailContext.tsx:125
 * `getPortfolio() => portfolioRef.current`, :138 `setPortfolio` writes the ref with
 * no state update), so the cockpit never re-renders when the snapshot lands. The
 * held combo then never resolves and the ticket keeps the flat single-leg form.
 * Measured: 3/3 runs stuck at "No position" for 80s after a click-through, 3/3
 * resolved in <2s on a document load. Suspected app bug — recorded, not worked
 * around by weakening any assertion below.
 */
async function openSpxuOrderTicket(page: import("@playwright/test").Page) {
  await page.goto(`/SPXU?posId=${SPXU_POSITION_ID}`);

  const ticket = page.locator(".act-ticket");
  await ticket.waitFor({ timeout: 30_000 });

  // `.act-ticket` is painted before the portfolio resolves, and at that point
  // OrderTab renders the flat single-leg "New Order" form
  // (components/ticker-detail/OrderTab.tsx:1088-1093) whose price field carries the
  // SAME `.modify-price-input` class as the combo form's net-limit field. Gate on
  // the combo submit button — it exists only once `isCombo` is true
  // (OrderTab.tsx:1080-1085, :989-991) — so every interaction below lands on the
  // combo ticket, never the stock form.
  await ticket.getByRole("button", { name: "Place Combo Order" }).waitFor({ timeout: 30_000 });
  return ticket;
}

/** Fill in the combo order form, then Place → Confirm. */
async function fillComboForm(ticket: import("@playwright/test").Locator, price: string) {
  const limitPriceInput = ticket.locator(".modify-price-input").first();
  await limitPriceInput.waitFor({ timeout: 10_000 });
  await limitPriceInput.fill(price);

  await ticket.getByRole("button", { name: "Place Combo Order" }).click();
  await ticket.getByRole("button", { name: "Confirm Order" }).click();
}

// ---------------------------------------------------------------------------
// Mock data the portfolio endpoint returns (SPXU Bull Call Spread)
// ---------------------------------------------------------------------------

const PORTFOLIO_MOCK = {
  bankroll: 50_000,
  peak_value: 52_000,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 5.78,
  total_deployed_dollars: 2891.57,
  remaining_capacity_pct: 94.22,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [
    {
      id: 23,
      ticker: "SPXU",
      structure: "Bull Call Spread $53.0/$60.0",
      structure_type: "Bull Call Spread",
      risk_profile: "defined",
      expiry: "2026-03-13",
      contracts: 20,
      direction: "DEBIT",
      entry_cost: 2891.57,
      max_risk: 2891.57,
      market_value: 3950.0,
      market_price_is_calculated: false,
      legs: [
        {
          direction: "LONG",
          contracts: 20,
          type: "Call",
          strike: 53.0,
          entry_cost: 4079.75,
          avg_cost: 203.99,
          market_price: 2.875,
          market_value: 5750.0,
          market_price_is_calculated: false,
        },
        {
          direction: "SHORT",
          contracts: 20,
          type: "Call",
          strike: 60.0,
          entry_cost: 1188.18,
          avg_cost: 59.41,
          market_price: 0.9,
          market_value: 1800.0,
          market_price_is_calculated: false,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-03-09",
    },
  ],
  exposure: {},
  violations: [],
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("SPXU combo order — rejection surfaces as error (RED → GREEN)", () => {
  // A cold Turbopack compile of /[ticker] plus the held-combo ticket resolving
  // off the portfolio fetch can exceed Playwright's 30s default; warm runs take
  // ~1s per test.
  test.setTimeout(90_000);

  test.beforeEach(async ({ page }) => {
    // Clear all previous route handlers so tests don't bleed into each other
    await page.unrouteAll({ behavior: "ignoreErrors" });

    // Dismiss any Next.js error overlays before the test starts
    await page.addInitScript(() => {
      window.addEventListener("error", (e) => e.preventDefault());
    });

    // Mock portfolio API
    await page.route("**/api/portfolio", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(PORTFOLIO_MOCK),
      }),
    );

    // Mock orders API (no open orders)
    await page.route("**/api/orders", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          last_sync: new Date().toISOString(),
          open_orders: [],
          executed_orders: [],
          open_count: 0,
          executed_count: 0,
        }),
      }),
    );

    // Mock prices WebSocket handshake (avoid connection error noise)
    await page.route("**/api/prices", (route) => route.abort());

    // Mock ancillary routes that the page may call
    await page.route("**/api/regime", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 0.2 }) }),
    );
    await page.route("**/api/ib-status", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
    );
    // The cockpit route pulls ticker metadata for the quote-bar fallback
    // (lib/useStockState.ts → /api/ticker/info). Stub the whole family so no
    // request reaches the live FastAPI bridge.
    await page.route("**/api/ticker/**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) }),
    );
    await page.route("**/api/blotter", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          as_of: new Date().toISOString(),
          summary: { realized_pnl: 0 },
          closed_trades: [],
          open_trades: [],
        }),
      }),
    );
  });

  test("RED: IB silent cancellation shows error instead of success", async ({ page }) => {
    // Mock placement — IB returns ok=true but initialStatus=Cancelled
    await page.route("**/api/orders/place", (route) =>
      route.fulfill({
        status: 502,
        contentType: "application/json",
        body: JSON.stringify({
          error: "Order rejected by IB: Cancelled",
          detail: { status: "ok", orderId: 12345, initialStatus: "Cancelled" },
        }),
      }),
    );

    const ticket = await openSpxuOrderTicket(page);
    await fillComboForm(ticket, "2.25");

    // Should show an error, not a success. `formatOrderError` maps
    // "Order rejected by IB: Cancelled" → summary "Order rejected by IB." +
    // detail "Cancelled." (web/lib/orderError.ts:63-65).
    const errorMsg = ticket.locator(".order-error");
    await errorMsg.waitFor({ timeout: 5_000 });
    await expect(errorMsg).toBeVisible();
    await expect(errorMsg).toContainText(/rejected|Cancelled/i);

    // Success toast should NOT appear
    await expect(page.locator(".toast-success")).not.toBeVisible();
  });

  test("GREEN: IB acceptance shows success message", async ({ page }) => {
    // Mock placement — IB accepts the order
    await page.route("**/api/orders/place", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "ok",
          orderId: 12345,
          permId: 67890,
          initialStatus: "Submitted",
          message: "SELL 20 SPXU @ $2.25 — Submitted",
          orders: {
            open_orders: [
              {
                orderId: 12345,
                permId: 67890,
                symbol: "SPXU Spread",
                contract: { symbol: "SPXU", secType: "BAG" },
                action: "SELL",
                orderType: "LMT",
                totalQuantity: 20,
                limitPrice: 2.25,
                status: "Submitted",
                filled: 0,
                remaining: 20,
                tif: "GTC",
              },
            ],
            executed_orders: [],
            open_count: 1,
            executed_count: 0,
          },
        }),
      }),
    );

    const ticket = await openSpxuOrderTicket(page);
    await fillComboForm(ticket, "2.25");

    // Should show a success toast (order feedback routes through the global
    // toast system, not an inline banner). The queue is drained into the toast
    // host on a 500ms interval (components/WorkspaceShell.tsx:410) and
    // auto-dismisses after 5s (lib/useToast.ts:44), so assert on the text in the
    // same wait rather than re-querying later.
    const successMsg = page.locator(".toast-success");
    await expect(successMsg).toContainText(/Combo order placed.*\$2\.25/i, { timeout: 10_000 });

    // Error should NOT appear
    await expect(ticket.locator(".order-error")).not.toBeVisible();
  });

  test("GREEN: Unknown status (no IB ack) shows error not success", async ({ page }) => {
    await page.route("**/api/orders/place", (route) =>
      route.fulfill({
        status: 502,
        contentType: "application/json",
        body: JSON.stringify({
          error: "Order rejected by IB: no acknowledgement (Unknown)",
          detail: { status: "ok", orderId: 0, initialStatus: "Unknown" },
        }),
      }),
    );

    const ticket = await openSpxuOrderTicket(page);
    await fillComboForm(ticket, "2.25");

    const errorMsg = ticket.locator(".order-error");
    await errorMsg.waitFor({ timeout: 5_000 });
    await expect(errorMsg).toBeVisible();
    await expect(page.locator(".toast-success")).not.toBeVisible();
  });

  test("GREEN: noisy upstream margin rejection is rendered as concise operator copy", async ({ page }) => {
    await page.route("**/api/orders/place", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({
          error:
            "Radon API 502: IB error 201: Order rejected - reason:YOUR ORDER IS NOT ACCEPTED. IN ORDER TO OBTAIN THE DESIRED POSITION YOUR PREVIOUS DAY EQUITY WITH LOAN VALUE <E> [644770.54 USD] MUST EXCEED THE INITIAL MARGIN [677243.00 USD].",
        }),
      }),
    );

    const ticket = await openSpxuOrderTicket(page);
    await fillComboForm(ticket, "2.25");

    const errorMsg = ticket.locator(".order-error");
    await errorMsg.waitFor({ timeout: 5_000 });
    await expect(errorMsg).toBeVisible();
    await expect(errorMsg).toContainText("Order rejected by IB: insufficient margin.");
    await expect(errorMsg).toContainText("$644,770.54");
    await expect(errorMsg).toContainText("$677,243.00");
    await expect(errorMsg).not.toContainText("Radon API 502");
    await expect(errorMsg).not.toContainText("YOUR ORDER IS NOT ACCEPTED");
  });
});
