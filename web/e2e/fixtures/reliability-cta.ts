/** REL-108: synthetic CTA fixtures; all API and relay transport stays local. */
import type { Page } from "@playwright/test";

export async function stubReliabilityCta(page: Page, bondPercentiles = [8, 9]) {
  await page.routeWebSocket(/:8765/, () => {});
  await page.route("**/api/**", route => route.fulfill({json: {}}));
  await page.route("**/api/portfolio", route => route.fulfill({json: {
    bankroll: 100000, positions: [], account_summary: {}, exposure: {}, violations: [], last_sync: new Date().toISOString(),
  }}));
  await page.route("**/api/orders", route => route.fulfill({json: {
    open_orders: [], executed_orders: [], open_count: 0, executed_count: 0,
  }}));
  await page.route("**/api/regime", route => route.fulfill({json: {
    cta: {exposure_pct: 90, realized_vol: 12, est_selling_bn: 1}, history: [],
  }}));
  const row = (underlying: string, percentile_3m: number) => ({
    underlying, percentile_3m, percentile_1m: 5, percentile_1y: 5,
    position_today: -1, position_yesterday: -1, position_1m_ago: 1, z_score_3m: -2,
  });
  await page.route("**/api/menthorq/cta", route => route.fulfill({json: {
    date: new Date().toISOString().slice(0, 10), fetched_at: new Date().toISOString(),
    tables: {main: [row('S&P E-mini', 5), ...bondPercentiles.map((p, i) => row(`Treasury ${i + 1}`, p))],
      index: [], commodity: [], currency: []},
    cache_meta: {is_stale: false, stale_reason: 'fresh'},
  }}));
}
