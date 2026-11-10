/**
 * E2E: Realized P&L card on /portfolio
 *
 * RED/GREEN TDD:
 * - RED: No fills today → Realized reads zero (not -$6,835 from IB account summary)
 * - GREEN: Fills present → shows correct sum; click card → fills modal appears
 *
 * SPEC REPAIR 2026-08-08 — selector/copy drift, no behavior change:
 *  1. The card lives in the TODAY'S P&L row and is labelled "Realized", not
 *     "Realized P&L", and the ACCOUNT row deliberately has no Realized card
 *     ("Account row (IB authoritative) — 4 cards, no Realized duplicate",
 *     components/MetricCards.tsx:96). The old `.metric-card` + hasText
 *     "Realized P&L" locator matched the ACCOUNT "Unrealized P&L" card —
 *     hasText is a case-insensitive SUBSTRING match and "Unrealized P&L"
 *     contains "realized P&L" — so every assertion read unrealized_pnl
 *     (-$212,251.69) and the click opened the unrealized modal.
 *     Cards are anchored on their exact label text and pinned to one match.
 *  2. Value formatting: cards render `fmtSigned` = sign + `fmt`
 *     (components/MetricCards.tsx:30-36), i.e. whole dollars with a leading
 *     sign — "+$0" / "+$258", never "$0.00". The modal total uses
 *     FillsModal's own `fmtPnl` (components/FillsModal.tsx:16-20) which IS
 *     2-decimal — "+$258.00".
 * Financial expectations are unchanged: zero stays zero, and the fill sum is
 * re-derived from the mock below rather than loosened.
 */

import { test, expect } from "@playwright/test";

/**
 * The Realized card, anchored on its exact label rather than a substring of
 * the row's text. `toHaveCount(1)` keeps a future duplicate/rename from
 * silently re-introducing the ambiguity this spec was rotting on.
 */
function realizedCard(page: import("@playwright/test").Page) {
  return page.locator(".metric-card").filter({ has: page.getByText("Realized", { exact: true }) });
}

// ── Shared mock data ────────────────────────────────────────────────────────

const PORTFOLIO_MOCK = {
  bankroll: 1_131_051.65,
  peak_value: 1_131_051.65,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 5.78,
  total_deployed_dollars: 2891.57,
  remaining_capacity_pct: 94.22,
  position_count: 1,
  defined_risk_count: 1,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [],
  exposure: {},
  violations: [],
  account_summary: {
    net_liquidation: 1_131_051.65,
    daily_pnl: -17_071.27,
    unrealized_pnl: -212_251.69,
    realized_pnl: -6_835.27,  // IB's number — should NOT be used
    settled_cash: -14_654.04,
    maintenance_margin: 513_065.33,
    excess_liquidity: 185_943.44,
    buying_power: 743_773.78,
    dividends: 910.0,
  },
};

const ORDERS_NO_FILLS = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

const ORDERS_WITH_FILLS = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [
    {
      execId: "exec-1",
      symbol: "AAPL",
      contract: { symbol: "AAPL", secType: "STK", currency: "USD", exchange: "SMART" },
      side: "BOT",
      quantity: 100,
      avgPrice: 214.50,
      commission: -1.05,
      realizedPNL: null,
      time: new Date().toISOString(),
      exchange: "SMART",
    },
    {
      execId: "exec-2",
      symbol: "AAPL",
      contract: { symbol: "AAPL", secType: "STK", currency: "USD", exchange: "SMART" },
      side: "SLD",
      quantity: 100,
      avgPrice: 219.25,
      commission: -1.05,
      realizedPNL: 473.0,
      time: new Date().toISOString(),
      exchange: "SMART",
    },
    {
      execId: "exec-3",
      symbol: "GOOG",
      contract: { symbol: "GOOG", secType: "OPT", currency: "USD", exchange: "SMART" },
      side: "SLD",
      quantity: 2,
      avgPrice: 4.50,
      commission: -2.10,
      realizedPNL: -215.0,
      time: new Date().toISOString(),
      exchange: "SMART",
    },
  ],
  open_count: 0,
  executed_count: 3,
};

// ── Helpers ──────────────────────────────────────────────────────────────────

async function setupBaseMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PORTFOLIO_MOCK) }),
  );
  await page.route("**/api/prices", (route) => route.abort());
  await page.route("**/api/regime", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ score: 15, level: "LOW", cri: { score: 15 } }) }),
  );
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ connected: false }) }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 3847.20 }, closed_trades: [], open_trades: [] }) }),
  );
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe("Realized P&L — fills-derived, not IB account summary", () => {
  test("RED: no fills today → Realized reads zero, not -$6,835", async ({ page }) => {
    await setupBaseMocks(page);
    await page.route("**/api/orders", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_NO_FILLS) }),
    );

    await page.goto("/portfolio");

    // The Realized card lives in the TODAY'S P&L row (MetricCards.tsx:494-497
    // live branch / :510-516 market-closed branch), not the ACCOUNT row.
    const card = realizedCard(page);
    await expect(card).toHaveCount(1, { timeout: 15_000 });

    // computeRealizedPnlFromFills([]) === 0 (lib/realized-pnl.ts:51), and
    // fmtSigned(0) === "+$0" (MetricCards.tsx:30-36 — `fmt` renders whole
    // dollars, so "$0.00" was never the rendered form). The financial point
    // stands: zero fills must NOT surface account_summary.realized_pnl.
    const value = card.locator(".metric-value");
    await expect(value).toHaveText("+$0");
    await expect(value).not.toContainText("6,835");
  });

  test("GREEN: fills present → Realized shows sum of fill P&L", async ({ page }) => {
    await setupBaseMocks(page);
    await page.route("**/api/orders", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_WITH_FILLS) }),
    );

    await page.goto("/portfolio");

    const card = realizedCard(page);
    await expect(card).toHaveCount(1, { timeout: 15_000 });

    // Derivation (lib/realized-pnl.ts:50-56 — sum realizedPNL over fills whose
    // ET date is today; null counts as 0):
    //   exec-1 AAPL BOT  realizedPNL null →     0
    //   exec-2 AAPL SLD  realizedPNL 473  →  +473
    //   exec-3 GOOG SLD  realizedPNL -215 →  -215
    //   total = 258 → fmtSigned(258) = "+$258"
    await expect(card.locator(".metric-value")).toHaveText("+$258");
  });

  test("GREEN: click Realized card → fills modal appears with breakdown", async ({ page }) => {
    await setupBaseMocks(page);
    await page.route("**/api/orders", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ORDERS_WITH_FILLS) }),
    );

    await page.goto("/portfolio");

    const card = realizedCard(page);
    await expect(card).toHaveCount(1, { timeout: 15_000 });
    await card.click();

    // FillsModal renders through Modal, which puts role="dialog" +
    // aria-labelledby={title} on the panel (components/Modal.tsx:76-83) and the
    // `fills-modal` class on the BACKDROP. Address it by role/name.
    const modal = page.getByRole("dialog", { name: "TODAY'S FILLS" });
    await expect(modal).toBeVisible({ timeout: 10_000 });

    // Should list the fills
    await expect(modal).toContainText("AAPL");
    await expect(modal).toContainText("GOOG");

    // Total row uses FillsModal's own 2-decimal fmtPnl (FillsModal.tsx:16-20):
    // 473 + (-215) = 258 → "+$258.00".
    await expect(modal.locator(".fills-total-value")).toContainText("+$258.00");
  });
});
