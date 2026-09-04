import { expect, test, type Page } from "@playwright/test";

/**
 * Desktop stock montage: ask SHARES and MARKET must not collide on a long MPID.
 * Repro (2026-09-04): AAOI inside ask rendered "195DRCTED...".
 *
 * Layout-only: load the ticker cockpit so globals.css is the production sheet,
 * then inject a 360px two-sided montage (cockpit book column with tape). A
 * live depth socket is not required for the spacing contract.
 */

const now = new Date().toISOString();

async function stubApis(page: Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  const json = (body: unknown) => (r: { fulfill: (o: object) => Promise<void> }) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/portfolio", json({
    bankroll: 100_000, peak_value: 100_000, last_sync: now, total_deployed_pct: 0,
    total_deployed_dollars: 0, remaining_capacity_pct: 100, position_count: 0,
    defined_risk_count: 0, undefined_risk_count: 0, avg_kelly_optimal: null,
    exposure: {}, violations: [], positions: [],
  }));
  await page.route("**/api/orders", json({ last_sync: now, open_orders: [], executed_orders: [] }));
  await page.route("**/api/regime", json({ score: 15, level: "LOW" }));
  await page.route("**/api/ib-status", json({ connected: true }));
  await page.route("**/api/blotter", json({ as_of: now, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }));
  await page.route("**/api/cash-flows**", json({ rows: [], summary: {} }));
  await page.route("**/api/flex-token", json({ ok: true, days_until_expiry: 14 }));
  await page.route("**/api/ticker/**", json({}));
}

test("ask shares and a long MPID stay in separate cells with a gap", async ({ page }) => {
  await stubApis(page);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/AAOI");
  await expect(page.locator(".book-window")).toBeVisible();

  const markup = `
    <div class="book-side bid">
      <div class="book-colhead">
        <span>Market</span>
        <span class="r">Shares</span>
        <span class="r">Bid</span>
      </div>
      <div class="book-row">
        <span class="book-mkt"><span class="book-venue-lead">NSDQ</span></span>
        <span class="book-shares">230</span>
        <span class="book-px">105.00</span>
      </div>
      <div class="book-row">
        <span class="book-mkt"><span class="book-venue-lead">DRCTEDARK</span></span>
        <span class="book-shares">127</span>
        <span class="book-px">104.97</span>
      </div>
    </div>
    <div class="book-side ask">
      <div class="book-colhead">
        <span>Ask</span>
        <span>Shares</span>
        <span class="r">Market</span>
      </div>
      <div class="book-row">
        <span class="book-px">105.24</span>
        <span class="book-shares">195</span>
        <span class="book-mkt"><span class="book-venue-lead">DRCTEDARK</span></span>
      </div>
      <div class="book-row">
        <span class="book-px">105.25</span>
        <span class="book-shares">105</span>
        <span class="book-mkt"><span class="book-venue-lead">IEXG</span></span>
      </div>
    </div>
  `;

  async function paint(width: number) {
    await page.evaluate(({ html, w }) => {
      const win = document.querySelector(".book-window");
      if (!win) throw new Error("book-window missing");
      win.innerHTML = `<div class="book-sides" style="width:${w}px">${html}</div>`;
    }, { html: markup, w: width });
  }

  async function askGap(): Promise<number> {
    const row = page.locator(".book-side.ask .book-row").first();
    await expect(row).toBeVisible();
    await expect(row.locator(".book-shares")).toHaveText("195");
    await expect(row.locator(".book-mkt")).toContainText("DRCTEDARK");
    return row.evaluate((el) => {
      const size = el.querySelector(".book-shares")!.getBoundingClientRect();
      const mkt = el.querySelector(".book-mkt")!.getBoundingClientRect();
      return mkt.left - size.right;
    });
  }

  // Tape-hidden desktop book column (~520px montage).
  await paint(520);
  expect(await askGap()).toBeGreaterThanOrEqual(6);
  await page.locator(".book-sides").screenshot({ path: "/tmp/book-montage-spacing.png" });

  // Tight montage (tape visible on a 1280px cockpit): still no overlap.
  await paint(360);
  expect(await askGap()).toBeGreaterThanOrEqual(6);
});
