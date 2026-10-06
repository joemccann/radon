import { expect, test } from '@playwright/test';
import { installPerformanceFixtures, performanceFixture } from './performance-fixtures';

test.beforeEach(async ({ page }) => installPerformanceFixtures(page));

test('renders flow-adjusted TWR, institutional metrics and measured NAV curve', async ({ page }) => {
  await page.goto('/performance');
  await expect(page.getByTestId('performance-hero-twr')).toHaveText('+5.09%');
  await expect(page.getByTestId('performance-hero-subtitle')).toContainText('Ending equity $182,217.36');
  await expect(page.getByTestId('performance-line-equity')).toHaveAttribute('d', /M.+L/);
  await expect(page.getByText('External Flows', { exact: true }).locator('..')).toContainText('$75,000');
  await expect(page.getByText('Methodology', { exact: true })).toBeVisible();
  await expect(page.getByTestId('performance-card-sharpe-vs-tbill')).toContainText('Sharpe');
  await expect(page.getByTestId('performance-card-max-drawdown')).toContainText('Max DD');
});

test('metric explainability uses flow-adjusted TWR and benchmark beta formulas', async ({ page }) => {
  await page.goto('/performance');
  const cards = page.locator('[data-testid^="performance-card-"]');
  await expect(cards).toHaveCount(10);
  await expect(cards.first()).toHaveClass(/metric-card-clickable/);
  await expect(cards.last()).toHaveClass(/metric-card-clickable/);
  await page.getByTestId('performance-card-twr-total').click();
  const modal = page.locator('.modal-content');
  await expect(modal).toContainText('external flows removed');
  await expect(modal).toContainText('TWR = Π(1+r_t)-1');
  await modal.getByRole('button', { name: 'Close' }).click();
  await expect(modal).toBeHidden();
  await page.getByTestId('performance-card-beta').click();
  await expect(modal).toContainText('Beta');
  await expect(modal).toContainText('Sensitivity');
  await expect(modal).toContainText('Beta = Cov(Portfolio, SPY) / Var(SPY)');
  await modal.getByRole('button', { name: 'Close' }).click();
  await expect(modal).toBeHidden();
  await cards.last().click();
  await expect(modal).toContainText('CVaR 95%');
  await expect(modal).toContainText('mean return on days at or below VaR 95%');
  await modal.getByRole('button', { name: 'Close' }).click();
  await expect(modal).toBeHidden();
});

test('revalidates a snapshot built before the portfolio session without copying live net liquidation into TWR', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-03-30T15:00:00Z'));
  await page.route('**/api/portfolio', route => route.fulfill({ json: { positions: [], last_sync: '2026-03-30T14:00:00Z', bankroll: 999999, account_summary: { net_liquidation: 999999 } } }));
  let reads = 0;
  await page.route('**/api/performance', route => { reads++; const payload = performanceFixture();
    if (reads > 1) payload.generated_at = '2026-03-30T14:05:00Z';
    return route.fulfill({ json: payload }); });
  await page.goto('/performance');
  await expect.poll(() => reads).toBeGreaterThan(1);
  await expect(page.getByTestId('performance-hero-twr')).toHaveText('+5.09%');
  await expect(page.getByTestId('performance-hero-subtitle')).toContainText('Ending equity $182,217.36');
});

test('a same-session snapshot does not trigger a rebuild for a later portfolio sync instant', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-03-30T15:00:00Z'));
  let reads = 0;
  await page.route('**/api/performance', route => { reads++; const payload = performanceFixture(); payload.generated_at = '2026-03-30T14:05:00Z'; return route.fulfill({ json: payload }); });
  await page.route('**/api/portfolio', route => route.fulfill({ json: { positions: [], last_sync: '2026-03-30T14:30:00Z', bankroll: 999999, account_summary: { net_liquidation: 999999 } } }));
  await page.goto('/performance');
  await expect(page.getByTestId('performance-hero-twr')).toHaveText('+5.09%');
  await page.clock.fastForward(60_000);
  expect(reads).toBe(1);
  await expect(page.getByTestId('performance-hero-subtitle')).toContainText('Ending equity $182,217.36');
});

test('route refresh replaces an unavailable measurement with the measured V2 NAV window', async ({ page }) => {
  let repaired = false; let reads = 0;
  await page.route('**/api/performance', route => { reads++; const payload = performanceFixture();
    if (!repaired) { payload.status = 'degraded'; payload.flows_status = 'failed'; payload.twr.cum_return = null as any; }
    return route.fulfill({ json: payload }); });
  await page.goto('/performance');
  await expect(page.getByTestId('performance-hero-twr')).toHaveText('--');
  const before = reads; repaired = true; await page.reload();
  await expect.poll(() => reads).toBeGreaterThan(before);
  await expect(page.getByTestId('performance-hero-twr')).toHaveText('+5.09%');
  await expect(page.getByTestId('performance-line-equity')).toHaveAttribute('d', /M.+L/);
});

test('More workspaces exposes the performance route', async ({ page }) => {
  await page.goto('/portfolio');
  await page.getByRole('button', { name: 'Open all workspaces', exact: true }).click();
  await page.getByRole('link', { name: 'Performance', exact: true }).click();
  await expect(page).toHaveURL(/\/performance$/);
  await expect(page.getByTestId('performance-hero-twr')).toHaveText('+5.09%');
});


test('portfolio sync advancing to a new session revalidates the measured performance snapshot', async ({ page }, testInfo) => {
  await page.clock.setFixedTime(new Date('2026-03-30T15:00:00Z'));
  let synced = false;
  const requests: string[] = [];
  await page.route('**/api/portfolio', route => {
    if (route.request().method() === 'POST') {
      requests.push('portfolio POST');
      synced = true;
    }
    return route.fulfill({ json: {
      positions: [], last_sync: synced ? '2026-03-31T15:00:00Z' : '2026-03-30T15:00:00Z',
      bankroll: 999999, account_summary: { net_liquidation: 999999 },
    } });
  });
  await page.route('**/api/performance', route => {
    requests.push('performance GET');
    const payload = performanceFixture();
    payload.generated_at = synced ? '2026-03-31T15:00:00Z' : '2026-03-30T14:05:00Z';
    return route.fulfill({ json: payload });
  });
  await page.goto('/performance');
  await expect(page.getByTestId('performance-hero-twr')).toHaveText('+5.09%');
  await expect(page.getByLabel('Workspace telemetry')).toContainText('11:00:00');
  // Fresh initial snapshots keep automatic stale recovery from consuming the
  // session transition intended for the explicit operator sync.
  expect(requests).toEqual(['performance GET']);
  const beforeClick = requests.length;
  await page.clock.setFixedTime(new Date('2026-03-31T15:00:00Z'));
  await page.getByRole('button', { name: 'Sync Now', exact: true }).click();
  await expect.poll(() => requests.slice(beforeClick)).toEqual(['portfolio POST', 'performance GET']);
  await testInfo.attach('portfolio-sync-performance-request-order', {
    body: JSON.stringify({ beforeClick, requests }), contentType: 'application/json',
  });
  await expect(page.getByTestId('performance-hero-twr')).toHaveText('+5.09%');
  await expect(page.getByTestId('performance-hero-subtitle')).toContainText('Ending equity $182,217.36');
});
