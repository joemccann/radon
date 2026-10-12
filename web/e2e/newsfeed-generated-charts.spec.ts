import { test, expect } from '@playwright/test';
import { installClearFixtures } from './clear-fixtures';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const reviewedPng = readFileSync(fileURLToPath(new URL('../tests/fixtures/research-generated-inflation.png', import.meta.url)));

const base = '/api/newsfeed/research/files/';
const png = base + 'b'.repeat(64) + '.png';
const source = {kind:'dropbox',publisher:'Synthetic Research Desk',url:base+'a'.repeat(64)+'.pdf',
  documentDate:'2026-10-09',folderDate:'2026-10-09',pages:[1],figures:[],fileId:'fixture',revision:'r1',contentHash:'c'.repeat(64)};
const oldPlan = {kind:'range',title:'Yields',unit:'%',axis:[0,6],ticks:[0,2,4,6],marks:[
  {id:'expectations-rose',label:'Expectations Rose',detail:'',low:4.35,high:4.35,estimated:false},
  {id:'from',label:'From',detail:'',low:4.15,high:4.15,estimated:false}]};
const content = 'Japan September consumer inflation expectations rose to 4.35% from 4.15% in August.';
const posts = [
  {id:'legacy',title:'Japan September Consumer Inflation Expectations Rose to 4.35% From 4.15% in August',content,
   timestamp:'2026-10-09T12:00:00Z',tags:['INFLATION'],images:[],source:{...source,charts:[oldPlan]}},
  {id:'reviewed',title:'Reviewed PNG inflation comparison',content,timestamp:'2026-10-09T11:00:00Z',tags:['INFLATION'],images:[png],
   source:{...source,chartGeneration:{version:1,status:'passed',charts:[],attempts:[]},
     figures:[{url:png,page:1,generated:true,caption:'Generated from source text: Japan consumer inflation expectations.'}]}},
  {id:'rejected',title:'Rejected chart remains text-only',content,timestamp:'2026-10-09T10:00:00Z',tags:['INFLATION'],images:[],
   source:{...source,charts:[oldPlan],chartGeneration:{version:1,status:'rejected',charts:[],attempts:[]}}},
];
for (const width of [1440, 390]) {
  test(`generated charts retain periods and review gates at ${width}px`, async ({ page }) => {
    test.setTimeout(120000);
    await page.setViewportSize({width,height:1000});
    await installClearFixtures(page);
    await page.route('**/api/newsfeed/posts**', route => route.fulfill({json:posts}));
    await page.route('**/api/newsfeed/research/files/*.png', route => route.fulfill({contentType:'image/png',
      body:reviewedPng}));
    await page.goto('/dashboard');
    const legacy = page.getByTestId('news-feed-item').filter({has:page.getByRole('heading',{name:posts[0].title,exact:true})});
    await expect(legacy).toBeVisible({timeout:60000});
    const chart = legacy.getByRole('img', {name:'Japan consumer inflation expectations'});
    await expect(chart).toBeVisible();
    await expect(chart).toContainText('August');
    await expect(chart).toContainText('September');
    await expect(chart).toContainText('4.15%');
    await expect(chart).toContainText('4.35%');
    const credit = chart.locator('a[href="https://radon.run"]');
    await expect(credit).toBeVisible();
    await expect(credit).toContainText('https://radon.run');
    await expect(legacy).not.toContainText('Yields');
    const reviewed = page.getByTestId('news-feed-item').filter({has:page.getByRole('heading',{name:posts[1].title,exact:true})});
    await expect(reviewed.locator('.news-feed-figure img')).toBeVisible();
    await expect(reviewed.locator('.news-feed-figure img')).toHaveJSProperty('naturalWidth',2400);
    await expect(reviewed.locator('[data-generated-charts]')).toHaveCount(0);
    await reviewed.getByRole('button',{name:`Open lightbox for: ${posts[1].title}`}).click();
    await expect(page.getByRole('dialog').locator('.newsfeed-lightbox__image')).toBeVisible();
    await expect(page.getByRole('dialog').locator('.newsfeed-lightbox__image')).toHaveJSProperty('naturalWidth',2400);
    await expect(page.getByRole('dialog')).toContainText('Generated from source text');
    await page.keyboard.press('Escape');
    const rejected = page.getByTestId('news-feed-item').filter({has:page.getByRole('heading',{name:posts[2].title,exact:true})});
    await expect(rejected.locator('[data-generated-charts]')).toHaveCount(0);
    await expect(rejected.locator('.news-feed-figure')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path:test.info().outputPath(`generated-charts-${width}.png`),fullPage:true});
  });
}
