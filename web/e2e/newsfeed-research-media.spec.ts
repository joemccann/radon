import { installClearFixtures } from "./clear-fixtures";
import { installClearRealtimeFixtures } from "./clear-realtime-fixtures";
import { test, expect } from "@playwright/test";
const base = "/api/newsfeed/research/files/";
const first = base + "a".repeat(64) + ".png";
const second = base + "b".repeat(64) + ".png";
const pdf = base + "c".repeat(64) + ".pdf";
const source = {kind:"dropbox",publisher:"Synthetic Bank — Research",url:pdf,documentDate:"2026-09-07",folderDate:"2026-09-07",pages:[2,3],figures:[{url:first,page:2,caption:"Positioning &#8212; across sectors"},{url:second,page:3,caption:"Weekly &mdash; distribution"}],fileId:"id:fixture",revision:"r1",contentHash:"c".repeat(64)};
const content = "## Investor flows\n\n• **Trust accounts** bought **over $14.5bn**.\n\n- **Investment trusts** remained buyers.\n  - *Retail investors* added $7.8bn.\n\nDemand &mdash; still firm.  \nRange: 10—20%. No rotation signal.";
const post = {id:"research-fixture",title:"New positioning evidence — still neutral",content,timestamp:"2026-09-07T16:00:00Z",images:[first,second],tags:["POSITIONING"],source};
const svg = (width:number,height:number) => `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 1000 600"><rect width="1000" height="600" fill="white"/><path d="M90 40V520H950" fill="none" stroke="#192b24" stroke-width="4"/><path d="M100 450L250 370L400 400L550 250L700 300L900 120" fill="none" stroke="#137c5d" stroke-width="6"/><text x="25" y="50" font-size="24">100</text><text x="30" y="520" font-size="24">0</text><text x="90" y="565" font-size="24">Jan</text><text x="900" y="565" font-size="24">Sep</text><text x="400" y="60" font-size="28">Synthetic chart</text></svg>`;
for (const width of [1440,393]) {
 test(`private research charts preserve source context at ${width}px`, async ({page}, testInfo) => {
   await page.setViewportSize({width,height:900});
   const optimizerRequests:string[]=[];
   page.on("request", request => { if(request.url().includes("/_next/image") && request.url().includes("research")) optimizerRequests.push(request.url()); });
   await page.route("**/api/newsfeed/posts**", route => route.fulfill({json:[post,{...post,id:"research-text",title:"Text evidence only",images:[],source:{...source,figures:[]}}]}));
   await page.route("**/api/newsfeed/research/files/*.png", route => route.fulfill({contentType:"image/svg+xml",body:svg(1000,600)}));
   await page.goto("/dashboard", {waitUntil:"domcontentloaded"});
   const item=page.getByTestId("news-feed-item").filter({hasText:"New positioning evidence, still neutral"});
   await expect(item.getByText("Synthetic Bank, Research · p. 2 · Positioning, across sectors")).toBeVisible();
   await expect(item.getByRole("link",{name:"Synthetic Bank, Research · Source PDF"})).toHaveAttribute("href",pdf);
   await expect(item).not.toContainText(/—|&(?:mdash|#8212|#x2014);/i);
   await expect(item).toContainText("Range: 10 to 20%.");
   await expect(page.getByText(/Text-only source evidence/)).toBeVisible();
   const body=item.locator(".news-feed-summary");
   await expect(body.getByRole("heading",{name:"Investor flows",level:2})).toBeVisible();
   await expect(body.locator("strong").first()).toHaveText("Trust accounts");
   await expect(body.locator("em")).toHaveText("Retail investors");
   await expect(body.locator("em")).toHaveCSS("font-style","italic");
   await expect(body.locator("em")).toHaveCSS("font-synthesis","style");
   await expect(body.locator("ul > li > ul > li")).toHaveText("Retail investors added $7.8bn.");
   await expect(body.locator("br")).toHaveCount(1);
   await expect(body).not.toContainText("**");
   await expect(page.getByTestId("news-feed-item").filter({hasText:"Text evidence only"}).locator(".news-feed-summary strong").first()).toHaveText("Trust accounts");
   const image=item.locator(".news-feed-image");
   await expect(image).toHaveJSProperty("naturalWidth",1000);
   expect(await image.evaluate(el=> Math.abs(el.getBoundingClientRect().width / el.getBoundingClientRect().height - 1000/600))).toBeLessThan(0.03);
   expect(await item.locator("figcaption span").evaluate(el => getComputedStyle(el).whiteSpace)).toBe("normal");
   await item.screenshot({path:testInfo.outputPath(`research-feed-${width}.png`)});
   await testInfo.attach(`research-feed-${width}`,{path:testInfo.outputPath(`research-feed-${width}.png`),contentType:"image/png"});
   await item.getByRole("button",{name:"Open chart 2: Weekly, distribution"}).click();
   await expect(page.locator(".newsfeed-lightbox__image")).toHaveJSProperty("src", new URL(second, page.url()).href);
   await expect(page.getByRole("dialog").getByText("Synthetic Bank, Research · p. 3 · Weekly, distribution")).toBeVisible();
   await expect(page.getByRole("dialog")).not.toContainText(/—|&(?:mdash|#8212|#x2014);/i);
   const lightboxBody=page.getByRole("dialog").locator(".newsfeed-lightbox__body");
   await expect(lightboxBody.getByRole("heading",{name:"Investor flows",level:2})).toBeVisible();
   await expect(lightboxBody.locator("strong").first()).toHaveText("Trust accounts");
   await expect(lightboxBody.locator("em")).toHaveText("Retail investors");
   await expect(lightboxBody.locator("em")).toHaveCSS("font-style","italic");
   await expect(lightboxBody.locator("em")).toHaveCSS("font-synthesis","style");
   await expect(lightboxBody.locator("ul > li > ul > li")).toHaveText("Retail investors added $7.8bn.");
   await expect(lightboxBody.locator("br")).toHaveCount(1);
   await expect(lightboxBody).not.toContainText("**");
   await expect(page.getByRole("dialog").getByRole("link",{name:"Source PDF",exact:true})).toHaveAttribute("href",pdf);
   await page.getByRole("button",{name:"View chart 1: Positioning, across sectors"}).click();
   await expect(page.locator(".newsfeed-lightbox__image")).toHaveJSProperty("src", new URL(first, page.url()).href);
   await page.screenshot({path:testInfo.outputPath(`research-lightbox-${width}.png`)});
   await testInfo.attach(`research-lightbox-${width}`,{path:testInfo.outputPath(`research-lightbox-${width}.png`),contentType:"image/png"});
   expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
   expect(optimizerRequests).toEqual([]);
   await page.keyboard.press("Escape");
   await expect(page.getByRole("dialog")).toHaveCount(0);
 });
}

for (const width of [1440, 393]) {
  test(`Market Ear chart captions preserve per-image providers at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const rampImage = "https://media.radon.run/images/adoption-fixture.png";
    const otherImage = "https://media.radon.run/images/uncredited-fixture.png";
    const posts = [
      {
        id: "market-ear-adoption",
        title: "Adoption slows",
        content: "In general, new AI adoption continues to grow but is decelerating.",
        timestamp: "2026-09-10T12:00:00Z",
        images: [rampImage],
        imageSources: { [rampImage]: "Ramp" },
        tags: ["AI"],
      },
      {
        id: "market-ear-uncredited",
        title: "Another chart without attribution",
        content: "The original post supplies no provider for this image.",
        timestamp: "2026-09-10T11:00:00Z",
        images: [otherImage],
        tags: ["AI"],
      },
    ];
    await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: posts }));
    await page.route("https://media.radon.run/images/*-fixture.png", route =>
      route.fulfill({ contentType: "image/svg+xml", body: svg(1000, 600) }));
    await page.route("**/_next/image?**", async route => {
      const imageUrl = new URL(route.request().url()).searchParams.get("url");
      if (imageUrl === rampImage || imageUrl === otherImage) {
        await route.fulfill({ contentType: "image/svg+xml", body: svg(1000, 600) });
      } else {
        await route.continue();
      }
    });
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    const credited = page.getByTestId("news-feed-item").filter({ hasText: "Adoption slows" });
    const uncredited = page.getByTestId("news-feed-item").filter({ hasText: "Another chart without attribution" });
    await expect(credited.locator(".news-feed-figcaption")).toContainText("Source: Ramp");
    await credited.scrollIntoViewIfNeeded();
    const image = credited.locator(".news-feed-image");
    // Optimized srcsets adjust natural dimensions for the selected pixel density.
    await expect.poll(() => image.evaluate(el => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    expect(await image.evaluate(el => {
      const img = el as HTMLImageElement;
      return img.naturalWidth / img.naturalHeight;
    })).toBeCloseTo(1000 / 600, 2);
    await expect(uncredited.locator(".news-feed-figcaption")).toHaveText("Chart · Another chart without attribution");
    await credited.screenshot({ path: testInfo.outputPath(`research-market-ear-feed-${width}.png`) });
    await testInfo.attach(`research-market-ear-feed-${width}`, {
      path: testInfo.outputPath(`research-market-ear-feed-${width}.png`), contentType: "image/png",
    });

    await credited.getByRole("button", { name: "Open lightbox for: Adoption slows" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.locator(".newsfeed-lightbox__media").getByText("Source: Ramp", { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`research-market-ear-lightbox-${width}.png`), animations: "disabled" });
    await testInfo.attach(`research-market-ear-lightbox-${width}`, {
      path: testInfo.outputPath(`research-market-ear-lightbox-${width}.png`), contentType: "image/png",
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

    await dialog.getByRole("button", { name: "Next article" }).click();
    await expect(dialog.getByRole("heading", { name: "Another chart without attribution" })).toBeVisible();
    await expect(dialog.locator(".newsfeed-lightbox__media")).not.toContainText("Source:");
    await expect(dialog).not.toContainText("Ramp");
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
  });
}

// Reader regressions stay in the existing curated CI research-media suite.
test.describe("Article reader gallery", () => {
const base = "/api/newsfeed/research/files/";
const first = base + "a".repeat(64) + ".png";
const second = base + "b".repeat(64) + ".png";
const pdf = base + "c".repeat(64) + ".pdf";
const post = {
 id: "reader-gallery", title: "Semiconductor fund flows accelerate", timestamp: new Date().toISOString(),
 content: "## Fund flows\n\n" + Array.from({length: 12}, (_, i) => `Paragraph ${i + 1}. Research charts and the accompanying analysis should remain readable without hiding the article controls. The source document provides the context for each observation.`).join("\n\n"),
 images: [first, second], tags: ["SEMICONDUCTORS", "ETF-FLOWS"],
 source: { kind: "dropbox", publisher: "JP Morgan", url: pdf, documentDate: "2026-10-27", folderDate: "2026-10-27", pages: [2, 3], figures: [{url:first, page:2, caption:"Sector contributions"}, {url:second, page:3, caption:"Weekly fund flows"}], fileId:"fixture", revision:"r1", contentHash:"c".repeat(64) }
};
const portrait = '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="1500"><rect width="600" height="1500" fill="white"/><text x="40" y="70" font-size="30">Sector contributions</text><path d="M90 150V1350H550" fill="none" stroke="#172624" stroke-width="3"/><path d="M140 1300V450H240V1300 M300 1300V800H400V1300" fill="#087f53"/><text x="40" y="1450" font-size="24">Illustrative test fixture</text></svg>';
const landscape = '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="600"><rect width="1200" height="600" fill="white"/><text x="60" y="60" font-size="32">Weekly fund flows</text><path d="M90 120V510H1120 M100 450L300 390L500 410L700 250L900 280L1100 150" fill="none" stroke="#087f53" stroke-width="5"/><text x="60" y="570" font-size="24">Illustrative test fixture</text></svg>';
for (const width of [375, 768, 1024, 1440]) for (const theme of ["dark", "light"]) {
 test.describe(`${width}px ${theme}`, () => {
 test.use({ hasTouch: width <= 768, isMobile: width === 375 });
 test(`article reader is bounded and gallery works at ${width}px ${theme}`, async ({page}, testInfo) => {
  await installClearFixtures(page);
  await installClearRealtimeFixtures(page);
  await page.route("**/api/bookmarks", r => r.fulfill({ json: { bookmarks: [] } }));
  await page.setViewportSize({width, height:900});
  await page.addInitScript(t => localStorage.setItem("theme", t), theme);
  await page.route("**/api/newsfeed/posts**", r => r.fulfill({json:[post, {...post,id:"next-article",title:"Next research article"}]}));
  await page.route("**/api/newsfeed/research/files/*.png", r => r.fulfill({contentType:"image/svg+xml", body:r.request().url().endsWith("a".repeat(64)+".png") ? portrait : landscape}));
  await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
  const opener=page.getByRole("button", {name:`Open lightbox for: ${post.title}`});
  await opener.click();
  const dialog=page.getByRole("dialog",{name:post.title});
  const frame=dialog.locator(".newsfeed-lightbox__frame");
  const rect=await frame.boundingBox();
  expect(rect).not.toBeNull();
  expect(rect!.y).toBeGreaterThanOrEqual(0);
  expect(rect!.y+rect!.height).toBeLessThanOrEqual(901);
  expect(Math.abs(rect!.y-(900-rect!.height)/2)).toBeLessThan(3);
  expect(Math.abs(rect!.x-(width-rect!.width)/2)).toBeLessThan(3);
  await expect(dialog.getByRole("button",{name:"Close article"})).toBeVisible();
  const gallery=dialog.getByRole("region",{name:"Article images"});
  await expect(gallery.getByRole("status")).toHaveText("Image 1 of 2");
  await gallery.getByRole("button",{name:"Next image"}).click();
  await expect(gallery.getByRole("status")).toHaveText("Image 2 of 2");
  await expect(dialog.locator(".newsfeed-lightbox__image")).toHaveJSProperty("src", new URL(second, page.url()).href);
  await page.keyboard.press("ArrowLeft");
  await expect(gallery.getByRole("status")).toHaveText("Image 1 of 2");
  expect(await dialog.locator(".newsfeed-lightbox__body").evaluate(el=>parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(16);
  if (width === 1440) {
   await page.setViewportSize({ width, height: 600 });
   const shortFrame = await frame.boundingBox();
   expect(shortFrame!.y + shortFrame!.height).toBeLessThanOrEqual(601);
   await expect(dialog.getByRole("button", { name: "Close article" })).toBeVisible();
   await page.setViewportSize({ width, height: 900 });
  }
  const restoredToast = page.locator(".toast").filter({ hasText: "IB Gateway · uplink restored" });
  if (await restoredToast.isVisible()) {
   await restoredToast.getByRole("button", { name: "Dismiss" }).click();
   await expect(restoredToast).toBeHidden();
  }
  await page.screenshot({path:testInfo.outputPath(`research-reader-${width}-${theme}.png`)});
  await testInfo.attach(`research-reader-${width}-${theme}`,{path:testInfo.outputPath(`research-reader-${width}-${theme}.png`),contentType:"image/png"});
  await gallery.getByRole("button",{name:"Enlarge image"}).click();
  await expect(dialog.getByRole("button",{name:"Back to article"})).toBeVisible();
  await gallery.getByRole("button",{name:"Zoom in"}).click();
  await expect(gallery.getByRole("button",{name:"Fit image"})).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog.getByRole("button",{name:"Enlarge image"})).toBeFocused();
  const source=dialog.getByRole("link",{name:"Source PDF",exact:true});
  await source.scrollIntoViewIfNeeded();
  await expect(source).toBeVisible();
  await expect(dialog.getByRole("button",{name:"Close article"})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await dialog.getByRole("button", { name: "Share", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Next article", exact: true })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Share", exact: true })).toBeFocused();
  await dialog.getByRole("button", { name: "Next article", exact: true }).click();
  const nextDialog = page.getByRole("dialog", { name: "Next research article" });
  await expect(nextDialog.getByRole("heading", { name: "Next research article" })).toBeVisible();
  expect(await nextDialog.locator(".newsfeed-lightbox__panel").evaluate(el => el.scrollTop)).toBe(0);
  await nextDialog.getByRole("button", { name: "Close article" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(opener).toBeFocused();
 });
 });
}

});

for (const width of [1440, 393]) {
  test(`MarketDesk article publishes before PDF at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await installClearFixtures(page);
    await installClearRealtimeFixtures(page);
    const articleUrl = "https://marketdesk.ai/library/browse/lpS7ST23Vhc";
    const authored = "$AAPL demand holds. $NVDA orders are unchanged.\n\nThe source leaves the outlook conditional on demand.";
    let attached = false;
    const articleSource = {
      kind: "marketdesk", evidenceKind: "article", publisher: "UBS", articleId: "lpS7ST23Vhc",
      articleUrl, url: articleUrl, contentHash: "d".repeat(64), documentDate: "2026-10-27", folderDate: "2026-10-27",
      pages: [], figures: [{ url: first, caption: "$AAPL demand chart" }],
    };
    await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [{
      id: "research-article-first", title: "$AAPL demand holds", content: authored,
      timestamp: "2026-10-27T16:26:36Z", images: [first], tags: ["MARKETDESK"],
      source: { ...articleSource, ...(attached ? { pdfUrl: pdf, pdfHash: "c".repeat(64) } : {}) },
    }, {
      id: "research-legacy-bullets", title: "Legacy authored article", content: "The authored summary remains.",
      timestamp: "2026-10-27T15:00:00Z", images: [], tags: ["MARKETDESK"],
      source: { ...articleSource, evidenceKind: undefined, pages: [1], url: pdf, contentHash: "c".repeat(64),
        figures: [], summary: ["RAW_SOURCE_BULLET_MUST_NOT_RENDER"] },
    }] }));
    await page.route("**/api/newsfeed/research/files/*.png", route => route.fulfill({ contentType: "image/svg+xml", body: svg(1000,600) }));
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    const item = page.getByTestId("news-feed-item").filter({ hasText: "$AAPL demand holds" });
    await expect(item.locator(".news-feed-summary")).toHaveText(authored);
    await expect(item.getByRole("link", { name: "UBS · Source article", exact: true })).toHaveAttribute("href", articleUrl);
    await expect(page.getByText("RAW_SOURCE_BULLET_MUST_NOT_RENDER", { exact: true })).toHaveCount(0);
    await page.getByRole("tab", { name: "PDFs", exact: true }).click();
    await expect(item.getByRole("heading", { name: "$AAPL demand holds", exact: true })).toBeVisible();
    await item.getByRole("button", { name: "Open lightbox for: $AAPL demand holds" }).click();
    const dialog = page.getByRole("dialog", { name: "$AAPL demand holds", exact: true });
    await expect(dialog.locator(".newsfeed-lightbox__body")).toHaveText(authored);
    await expect(dialog.getByRole("list", { name: "Summary" })).toHaveCount(0);
    await expect(dialog.getByRole("link", { name: "Source article", exact: true })).toHaveAttribute("href", articleUrl);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const screenshot = testInfo.outputPath(`marketdesk-article-first-${width}.png`);
    await page.screenshot({ path: screenshot });
    await testInfo.attach(`marketdesk-article-first-${width}`, { path: screenshot, contentType: "image/png" });
    await dialog.getByRole("button", { name: "Close article" }).click();
    attached = true;
    await page.getByRole("button", { name: "Refresh feed", exact: true }).click();
    await expect(item.getByRole("link", { name: "UBS · Source PDF", exact: true })).toHaveAttribute("href", pdf);
    await expect(item.locator(".news-feed-summary")).toHaveText(authored);
  });
}
