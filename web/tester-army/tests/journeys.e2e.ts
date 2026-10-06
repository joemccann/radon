import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { readFileSync, readdirSync } from 'node:fs';
import { relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const samples = JSON.parse(readFileSync(new URL('../fixtures.json', import.meta.url), 'utf8'));
import { surfaceOf } from '@e2e-dev/web';
import { engine } from '../e2e.config.ts';
let runtimeErrors: string[] = [];
test.beforeEach(async ({ app, browser }) => {
  await app.open('/manifest.webmanifest');
  const page = surfaceOf(engine)!.page();
  runtimeErrors = [];
  page.on('pageerror', error => runtimeErrors.push(error.message));
  await page.clock.setFixedTime(new Date('2026-09-04T18:00:00Z'));
  await page.addInitScript(samples.init);
  await browser.route('**/api/**', async route => {
    const path = new URL(route.request.url).pathname;
    const body = samples.fixtures[path];
    await route.fulfill({ status: body ? 200 : 503, json: body ?? { error: 'No sample measurement is available for this source.', missing: true } });
  });
  await page.routeWebSocket(/\/ws(?:\?|$)|(?:localhost|127\.0\.0\.1):(?:8765|8766|18765|18766)/, socket => {
    socket.onMessage(raw => {
      const message = JSON.parse(raw.toString());
      if (message.action === 'search') socket.send(JSON.stringify({ type: 'searchResults', pattern: message.pattern, results: [{ symbol: 'AAPL', conId: 265598, secType: 'STK', exchange: 'SMART', currency: 'USD', description: 'Apple Inc.' }] }));
      if (message.action === 'subscribe-depth' && message.symbol === 'AAPL') {
        const level = (price: number) => ({ price, size: 100, marketMaker: 'ARCA', exchange: 'ARCA' });
        socket.send(JSON.stringify({ type: 'depth-batch', updates: { AAPL: { symbol: 'AAPL', kind: 'stock', isSmartDepth: true, feed: 'SMART DEPTH', entitled: true, timestamp: '2026-09-04T18:00:00Z', bid: [level(232.17)], ask: [level(232.19)] } } }));
      }
      if (message.action === 'subscribe' || message.action === 'snapshot') {
        socket.send(JSON.stringify({ type: 'status', ib_connected: true, ib_issue: null }));
        const updates = Object.fromEntries((message.symbols ?? []).map((symbol: string) => [symbol, { symbol, last: 232.18, bid: 232.17, ask: 232.19, close: 230.34, bidSize: 100, askSize: 100, timestamp: '2026-09-04T18:00:00Z' }]));
        socket.send(JSON.stringify({ type: 'batch', updates }));
      }
    });
  });
  const origin = new URL(app.baseUrl!).origin;
  await page.context().route(/^https?:/, route => new URL(route.request().url()).origin === origin ? route.fallback() : route.abort());
});
test.afterEach(() => { expect(runtimeErrors).toEqual([]); });

test('options measurement tabs preserve selected ticker and browser history', async ({ app, screen, browser }) => {
  await app.open('/options');
  await expect(screen.getByRole('button', { name: 'Load exposure' })).toBeDisabled();
  await screen.getByLabel('Ticker symbol').fill('aapl');
  await screen.getByRole('button', { name: 'Load exposure' }).click();
  await expect(browser).toHaveURL(/\/options\/net-gex\?symbol=AAPL$/);
  await expect(screen.getByTestId('options-exposure-table-wrap')).toBeVisible();
  await screen.getByRole('tab', { name: 'Rel Vol' }).click();
  await expect(screen.getByTestId('rv-ratio-stats')).toBeVisible();
  await browser.back();
  await expect(browser).toHaveURL(/\/options\/net-gex\?symbol=AAPL$/);
  await app.screenshot('options-history');
});
test('journal filters populated and empty ranges without losing realized totals', async ({ app, screen }) => {
  await app.open('/journal');
  await screen.getByTestId('journal-range-all').click();
  await expect(screen.getByTestId('journal-trade-count')).toHaveText('2 TRADES');
  await expect(screen.getByTestId('journal-range-realized')).toContainText('380');
  await screen.getByTestId('journal-range-mtd').click();
  await expect(screen.getByTestId('journal-trade-count')).toHaveText('0 TRADES');
  await expect(screen.getByTestId('journal-range-empty')).toBeVisible();
  await app.screenshot('journal-empty-range');
});

for (const route of samples.routes.filter((route: { guarded?: boolean }) => !route.guarded)) {
  test(`route ${route.path} renders its measurement or explicit empty state`, async ({ app, browser, screen }) => {
    await app.open(route.path);
    if (route.destination) await expect(browser).toHaveURL(new RegExp(route.destination.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$'));
    if (route.path === '/journal') await screen.getByTestId('journal-range-all').click();
    if (route.selector) await expect(browser.locator(`${route.selector}:visible`).first()).toBeVisible();
    if (route.text) await expect(screen.getByText(route.text, { exact: false, visible: true }).first()).toBeVisible();
    await app.screenshot(`route-${route.path.replace(/[^a-z0-9]/gi, '-')}`);
  });
}

for (const width of [1440, 390]) {
  test(`watchlist sort and instrument navigation at ${width}px`, async ({ app, browser, screen }) => {
    await browser.setViewport({ width, height: 844 });
    await app.open('/watchlist');
    await expect(screen.getByTestId('watchlist-row-AAPL')).toBeVisible();
    await expect(screen.getByTestId('watchlist-row-MSFT')).toBeVisible();
    if (width > 640) {
      await screen.getByRole('button', { name: 'Sort by Symbol' }).click();
      await screen.getByRole('button', { name: 'Sort by Symbol' }).click();
      await expect(browser.locator('[data-testid^="watchlist-row-"]').first()).toHaveAttribute('data-testid', 'watchlist-row-MSFT');
    }
    await screen.getByRole('button', { name: 'Open MSFT instrument cockpit' }).click();
    await expect(browser).toHaveURL(/\/MSFT$/);
    await expect(screen.getByText('MSFT', { visible: true }).first()).toBeVisible();
    await app.screenshot(`watchlist-instrument-${width}`);
  });
}

test('preferences reject out-of-band drafts, retain failed save and persist retry/reset payloads', async ({ app, browser, screen }) => {
  const payload = structuredClone(samples.fixtures['/api/preferences']);
  const bodies: unknown[] = [];
  let reject = true;
  await browser.route('**/api/preferences*', async route => {
    if (route.request.method === 'GET') return route.fulfill({ json: payload });
    bodies.push({ method: route.request.method, data: route.request.postData, url: route.request.url });
    if (reject) { reject = false; return route.fulfill({ status: 422, json: { error: 'Synthetic preference rejection' } }); }
    const value = route.request.method === 'DELETE' ? 500 : JSON.parse(route.request.postData!).value;
    payload.preferences[0].value = value;
    await route.fulfill({ json: { preference: payload.preferences[0], store: payload.store } });
  });
  await app.open('/preferences');
  const input = screen.getByTestId('preference-input-RADON_MAX_ORDER_QTY');
  const save = screen.getByTestId('preference-save-RADON_MAX_ORDER_QTY');
  await expect(input).toHaveValue('400');
  await input.fill('0');
  await expect(save).toBeDisabled();
  expect(bodies).toHaveLength(0);
  await input.fill('300');
  await save.click();
  await expect(screen.getByTestId('preference-error-RADON_MAX_ORDER_QTY')).toBeVisible();
  await expect(input).toHaveValue('300');
  await save.click();
  await expect(screen.getByTestId('preference-error-RADON_MAX_ORDER_QTY')).toBeHidden();
  await expect(save).toBeDisabled();
  expect(bodies.slice(0, 2)).toEqual([0, 1].map(() => ({ method: 'PUT', url: new URL('/api/preferences', app.baseUrl!).href, data: JSON.stringify({ key: 'RADON_MAX_ORDER_QTY', value: 300 }) })));
  await screen.getByTestId('preference-reset-RADON_MAX_ORDER_QTY').click();
  await expect(input).toHaveValue('500');
  expect(bodies[2]).toEqual({ method: 'DELETE', url: new URL('/api/preferences?key=RADON_MAX_ORDER_QTY', app.baseUrl!).href, data: undefined });
  await app.screenshot('preferences-retry-reset');
});

test('profile failed save keeps draft and retries the exact username payload', async ({ app, browser, screen }) => {
  const writes: unknown[] = [];
  await browser.route('**/api/profile', async route => {
    if (route.request.method === 'GET') return route.fulfill({ json: samples.fixtures['/api/profile'] });
    writes.push({ url: route.request.url, method: route.request.method, body: JSON.parse(route.request.postData!) });
    await route.fulfill(writes.length === 1 ? { status: 503, json: { error: 'Synthetic save failure' } } : { json: { username: 'Verified Operator', avatar_url: null } });
  });
  await browser.route('**/api/bookmarks', route => route.fulfill({ json: { bookmarks: [] } }));
  await app.open('/profile');
  const username = screen.getByRole('textbox', { name: /^Username/ });
  await username.fill('Verified Operator');
  await username.press('Enter');
  await expect(screen.getByRole('alert').filter({ hasText: 'Could not save. Try again.' })).toBeVisible();
  await expect(username).toHaveValue('Verified Operator');
  await username.focus();
  await username.press('Enter');
  await expect(screen.getByRole('alert').filter({ hasText: 'Could not save. Try again.' })).toBeHidden();
  expect(writes).toEqual([0, 1].map(() => ({ url: new URL('/api/profile', app.baseUrl!).href, method: 'PUT', body: { username: 'Verified Operator' } })));
  await app.screenshot('profile-save-retry');
});

test('newsfeed filters posts, bookmarks with payload, opens media and dismisses it', async ({ app, browser, screen }) => {
  const image = '/api/newsfeed/research/files/' + 'a'.repeat(64) + '.png';
  const posts = [
    { id: 'fixture-news-1', title: 'Synthetic hedge demand', content: 'Measured positioning remains neutral.', timestamp: '2026-09-04T16:00:00Z', images: [image], tags: ['JPY'] },
    { id: 'fixture-news-2', title: 'Synthetic equity flows', content: 'Measured equity flow sample.', timestamp: '2026-09-04T15:00:00Z', images: [], tags: ['EQUITY'] },
  ];
  let saved: unknown[] = [];
  const writes: unknown[] = [];
  await browser.route('**/api/newsfeed/posts*', route => route.fulfill({ json: posts }));
  await browser.route('**/api/newsfeed/research/files/*', route => route.fulfill({ headers: { 'content-type': 'image/svg+xml' }, body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400"><rect width="600" height="400" fill="white"/><text x="20" y="30">Synthetic chart</text></svg>' }));
  await browser.route('**/_next/image*', route => route.fulfill({ headers: { 'content-type': 'image/svg+xml' }, body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400"><rect width="600" height="400" fill="white"/><text x="20" y="30">Synthetic chart</text></svg>' }));
  await browser.route('**/api/bookmarks*', async route => {
    if (route.request.method === 'POST') {
      const body = JSON.parse(route.request.postData!); writes.push({ url: route.request.url, method: route.request.method, body });
      saved = [{ id: 'bookmark-1', post_id: body.post_id, snapshot: body.snapshot, saved_at: '2026-09-04T18:00:00Z' }];
      return route.fulfill({ json: saved[0] as any });
    }
    await route.fulfill({ json: { bookmarks: saved } as any });
  });
  await app.open('/dashboard');
  await expect(screen.getByTestId('news-feed-item')).toHaveCount(2);
  await screen.getByRole('button', { name: 'JPY', exact: true }).click();
  await expect(screen.getByTestId('news-feed-item')).toHaveCount(1);
  const article = screen.getByTestId('news-feed-item');
  expect(writes).toHaveLength(0);
  await article.getByTestId('star-toggle').click();
  await expect(article.getByTestId('star-toggle')).toHaveAttribute('aria-pressed', 'true');
  expect(writes).toEqual([{ url: new URL('/api/bookmarks', app.baseUrl!).href, method: 'POST', body: { post_id: posts[0].id, snapshot: { title: posts[0].title, source: 'https://themarketear.com/posts/fixture-news-1', timestamp: '2026-09-04T16:00:00.000Z', image, thumbnail: image } } }]);
  await screen.getByRole('button', { name: 'Open lightbox for: Synthetic hedge demand' }).click();
  await expect(screen.getByRole('dialog', { name: 'Synthetic hedge demand' })).toBeVisible();
  await expect(screen.getByRole('dialog', { name: 'Synthetic hedge demand' }).getByRole('image', { name: 'Synthetic hedge demand' })).toBeVisible();
  await expect.poll(() => browser.evaluate(() => [...document.querySelectorAll<HTMLImageElement>('[role=dialog] img')].every(image => image.complete && image.naturalWidth > 0))).toBe(true);
  await expect.poll(() => browser.evaluate(() => getComputedStyle(document.querySelector('.newsfeed-lightbox')!).opacity)).toBe('1');
  await app.screenshot('news-media-bookmark');
  await browser.keyboard.press('Escape');
  await expect(screen.getByRole('dialog', { name: 'Synthetic hedge demand' })).toBeHidden();
});

test('scanner mode exposes discovery and routes selected candidate to its cockpit', async ({ app, browser, screen }) => {
  await app.open('/scanner?mode=discover');
  await expect(screen.getByTestId('discover-order-link-MSFT')).toBeVisible();
  await screen.getByTestId('discover-order-link-MSFT').click();
  await expect(browser).toHaveURL(/\/MSFT(?:\?.*)?$/);
  await app.screenshot('scanner-discovery-cockpit');
});

test('stock ticket risk verification never transmits before confirmation and preserves payload', async ({ app, browser, screen }) => {
  const placed: unknown[] = [];
  await browser.route('**/api/orders/place', async route => {
    placed.push({ url: route.request.url, method: route.request.method, body: JSON.parse(route.request.postData!) });
    await route.fulfill({ json: { status: 'ok', orderId: 9911, initialStatus: 'Submitted' } });
  });
  await app.open('/SPY?tab=order');
  const ticket = browser.locator('.order-form').first();
  await expect(ticket).toBeVisible();
  await ticket.getByRole('spinbutton').first().fill('1');
  await browser.locator('.order-form .modify-price-input').fill('230');
  await ticket.getByRole('button', { name: 'Place Order' }).click();
  expect(placed).toHaveLength(0);
  await expect(ticket.getByRole('button', { name: 'Confirm Order' })).toBeVisible();
  await ticket.getByRole('button', { name: 'Confirm Order' }).scrollIntoView();
  await app.screenshot('stock-risk-confirm');
  await ticket.getByRole('button', { name: 'Confirm Order' }).click();
  await expect(ticket.getByRole('button', { name: 'Confirm Order' })).toBeHidden();
  expect(placed).toEqual([{ url: new URL('/api/orders/place', app.baseUrl!).href, method: 'POST', body: { type: 'stock', symbol: 'SPY', action: 'BUY', quantity: 1, limitPrice: 230, tif: 'DAY' } }]);
});

test('keyboard ticker search connects lazily, selects result and opens instrument', async ({ app, browser, screen }) => {
  await app.open('/portfolio');
  await browser.keyboard.press('ControlOrMeta+k');
  const input = screen.getByRole('combobox', { name: 'Search ticker' });
  await expect(input).toBeFocused();
  await input.fill('AAPL');
  await expect(screen.getByRole('option').first()).toContainText('AAPL');
  await input.press('ArrowDown');
  await input.press('Enter');
  await expect(browser).toHaveURL(/\/AAPL(?:\?.*)?$/);
  await app.screenshot('ticker-keyboard-selection');
});

// Filesystem inventory is intentional; this does not reconstruct module imports.
test('coverage manifest includes every current App Router page exactly once', () => {
  const root = new URL('../../app/', import.meta.url);
  const pages = readdirSync(root, { recursive: true, withFileTypes: true })
    .filter(entry => entry.isFile() && entry.name === 'page.tsx')
    .map(entry => 'app/' + relative(fileURLToPath(root), resolve(entry.parentPath, entry.name)).replaceAll('\\', '/')).sort();
  expect(samples.routes.map((route: { source: string }) => route.source).sort()).toEqual(pages);
});

test('scanner parses explicit tickers, rejects odd pairs and posts only valid requests', async ({ app, browser, screen }) => {
  const scans: unknown[] = [];
  await browser.route('**/api/garch-convergence/scan', async route => {
    scans.push({ url: route.request.url, method: route.request.method, body: JSON.parse(route.request.postData!) });
    await route.fulfill({ json: samples.fixtures['/api/garch-convergence'] });
  });
  await app.open('/scanner?mode=garch');
  const section = screen.getByTestId('garch-scanner-section');
  await section.getByLabel('Ticker symbols').fill('NVDA, AMD, TSM');
  await section.getByRole('button', { name: 'Scan' }).click();
  await expect(screen.getByRole('alert').filter({ hasText: 'Enter pairs: an even number of tickers.' })).toBeVisible();
  expect(scans).toHaveLength(0);
  await section.getByLabel('Ticker symbols').fill('nvda, amd');
  await section.getByRole('button', { name: 'Scan' }).click();
  await expect(section.getByTestId('garch-row-NVDA-AMD')).toBeVisible();
  expect(scans).toEqual([{ url: new URL('/api/garch-convergence/scan', app.baseUrl!).href, method: 'POST', body: { tickers: ['NVDA', 'AMD'] } }]);
  await app.screenshot('scanner-pair-validation');
});

test('cold measurement failure presents retry, then restores populated data', async ({ app, browser, screen }) => {
  let reads = 0;
  let recover = false;
  await browser.route('**/api/options/exposure*', async route => {
    reads += 1;
    await route.fulfill(!recover ? { status: 503, json: { error: 'Synthetic measurement unavailable' } } : { json: samples.fixtures['/api/options/exposure'] });
  });
  await app.open('/options/net-gex?symbol=AAPL');
  await expect(screen.getByRole('button', { name: 'Retry measurement' })).toBeVisible();
  await expect(screen.getByRole('alert').filter({ hasText: 'This service is temporarily unavailable. Please try again shortly.' })).toBeVisible();
  await app.screenshot('measurement-failure-retry');
  recover = true;
  await screen.getByRole('button', { name: 'Retry measurement' }).click();
  await expect(screen.getByTestId('options-exposure-table-wrap')).toBeVisible();
  expect(reads).toBeGreaterThanOrEqual(2);
});


