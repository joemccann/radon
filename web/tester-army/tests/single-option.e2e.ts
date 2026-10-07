import { test, surfaceOf } from '@e2e-dev/web';
import { expect } from 'e2e';
import { engine } from '../e2e.config.ts';
import { readFileSync } from 'node:fs';
// Native transport fixtures are shared; all user actions/assertions use the SDK.
test("single-option SELL preserves native bid/ask and transmits only the reviewed payload", async ({ app, browser, screen }) => {
  const { PRICE_FIXTURES, stubApis } = await import('../../e2e/crcl-spread-builder-fixtures.ts');
  await app.open("/manifest.webmanifest");
  const page = surfaceOf(engine)!.page();
  const sample = JSON.parse(readFileSync(new URL('../fixtures.json', import.meta.url), 'utf8'));
  await page.context().addInitScript(sample.init);
  const fixtureTime = '2026-05-04T14:00:00Z';
  await page.clock.setFixedTime(new Date(fixtureTime));
  const origin = new URL(app.baseUrl!).origin;
  await page.context().route(/^https?:/, route => new URL(route.request().url()).origin === origin ? route.fallback() : route.abort());
  await page.routeWebSocket(/\/ws(?:\?|$)|(?:localhost|127\.0\.0\.1):(?:8765|8766|18765|18766)/, socket => {
    const send = () => {
      socket.send(JSON.stringify({ type: 'status', ib_connected: true, ib_issue: null }));
      socket.send(JSON.stringify({ type: 'batch', updates: Object.fromEntries(Object.entries(PRICE_FIXTURES).map(([key,value]) => [key, { ...value, timestamp: fixtureTime }])) }));
    };
    socket.onMessage(raw => { if (['subscribe','snapshot'].includes(JSON.parse(String(raw)).action)) send(); });
    send();
  });

  const requests: unknown[] = [];
  await browser.route('**/api/**', route => {
    const url = new URL(route.request.url);
    if (url.pathname === '/api/orders/place') {
      requests.push({ url: route.request.url, method: route.request.method, body: JSON.parse(route.request.postData!) });
      return route.fulfill({ json: { status: 'ok', orderId: 9912, initialStatus: 'Submitted' } });
    }
    const body = sample.fixtures[url.pathname];
    return route.fulfill({ status: body ? 200 : 503, json: body ?? { missing: true, error: 'No sample measurement is available for this source.' } });
  });
  await stubApis(page as any, fixtureTime);
  await page.unroute('**/api/**');
  expect(await browser.evaluate(async () => (await fetch('/api/tester-army-unknown-fixture')).status)).toBe(503);
  await app.open('/CRCL?tab=chain');
  await expect(browser).toHaveURL(/expiry=2026-06-18/);
  await expect(browser.locator('select[aria-label="Options expiry"] option[value="20260618"]')).not.toContainText("(-");
  await expect(browser.locator('.chain-grid tr[data-strike="140"] .chain-bid[title="Sell call"]').first()).toHaveText("$7.00");
  await browser.locator('.chain-grid tr[data-strike="140"] .chain-bid[title="Sell call"]').first().click();
  await expect(browser.locator('.order-builder .modify-price-input')).toHaveValue('8.00');
  for (const quote of ['BID 7.00', 'MID 8.00', 'ASK 9.00']) await expect(screen.getByRole('button', { name: quote, exact: true })).toBeEnabled();
  expect(requests).toHaveLength(0);
  await browser.locator('.order-builder .modify-price-input').fill('0.01');
  await expect(browser.locator('.order-builder .ticket-risk-cell').filter({ hasText: 'BREAKEVENS' })).toContainText('140.01');
  expect(requests).toHaveLength(0);
  await browser.locator('.order-builder .modify-price-input').fill('8.00');
  await screen.getByTestId('ticket-verify').click();
  expect(requests).toHaveLength(0);
  // The fixture's 40 lower-strike long calls cover this one-contract SELL.
  await expect(screen.getByTestId('ticket-unbounded-ack')).toHaveCount(0);
  await expect(screen.getByTestId('ticket-transmit')).toBeEnabled();
  await expect(browser.locator('.order-builder .ticket-risk-payoff-wrap')).toContainText('AT EXPIRY · ORDER LEGS ONLY · PER 1× COMBO');
  await expect(browser.locator('.order-builder .ticket-risk-cell').filter({ hasText: 'ORDER BREAKEVENS' })).toContainText('148.00');
  expect(requests).toHaveLength(0);
  await page.getByTestId('ticket-transmit').evaluate(el => el.scrollIntoView({ block: 'center', inline: 'nearest' }));
  await expect.poll(async () => page.getByTestId('ticket-transmit').evaluate(el => { const rect = el.getBoundingClientRect(); const footer = document.querySelector('.footer-strip')?.getBoundingClientRect(); return rect.top >= 48 && rect.bottom <= (footer?.top ?? innerHeight); })).toBe(true);
  await expect(browser.locator('.toast')).toHaveCount(0);
  await app.screenshot('single-sell-native-book-review');
  await screen.getByTestId('ticket-transmit').click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests).toEqual([{ url: new URL('/api/orders/place', app.baseUrl!).href, method: 'POST', body: { type: 'option', symbol: 'CRCL', expiry: '20260618', strike: 140, right: 'CALL', action: 'SELL', quantity: 1, limitPrice: 8, tif: 'DAY' } }]);
});
