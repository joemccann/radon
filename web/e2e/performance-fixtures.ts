import type { Page } from '@playwright/test';
import { goldenOkPayload } from '../tests/fixtures/performanceScenarios';
import { installClearFixtures } from './clear-fixtures';

export function performanceFixture() { return goldenOkPayload(); }
export async function installPerformanceFixtures(page: Page) {
  await installClearFixtures(page);
  await page.clock.setFixedTime(new Date('2026-03-27T22:00:00Z'));
  await page.route('**/api/portfolio', route => route.fulfill({ json: {
    positions: [], last_sync: '2026-03-27T21:00:00Z', bankroll: 182217.35574011656,
    account_summary: { net_liquidation: 182217.35574011656, daily_pnl: 0, settled_cash: 100000 },
  } }));
  await page.route('**/api/performance', route => route.fulfill({ json: performanceFixture() }));
}
export async function resolvedColor(page: Page, variable: string) {
  return page.evaluate(variable => {
    const node = document.createElement('span'); node.style.color = `var(${variable})`;
    document.body.append(node); const color = getComputedStyle(node).color; node.remove(); return color;
  }, variable);
}
