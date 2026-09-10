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
   await expect(page.locator(".newsfeed-lightbox__image")).toHaveAttribute("src",second);
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
   await expect(page.locator(".newsfeed-lightbox__image")).toHaveAttribute("src",first);
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
    await expect(credited.locator(".news-feed-image")).toHaveJSProperty("naturalWidth", 1000);
    await expect(uncredited.locator(".news-feed-figcaption")).toHaveText("Chart · Another chart without attribution");
    await credited.screenshot({ path: testInfo.outputPath(`research-market-ear-feed-${width}.png`) });
    await testInfo.attach(`research-market-ear-feed-${width}`, {
      path: testInfo.outputPath(`research-market-ear-feed-${width}.png`), contentType: "image/png",
    });

    await credited.getByRole("button", { name: "Open lightbox for: Adoption slows" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.locator(".newsfeed-lightbox__media").getByText("Source: Ramp", { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`research-market-ear-lightbox-${width}.png`) });
    await testInfo.attach(`research-market-ear-lightbox-${width}`, {
      path: testInfo.outputPath(`research-market-ear-lightbox-${width}.png`), contentType: "image/png",
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

    await dialog.getByRole("button", { name: "Next post" }).click();
    await expect(dialog.getByRole("heading", { name: "Another chart without attribution" })).toBeVisible();
    await expect(dialog.locator(".newsfeed-lightbox__media")).not.toContainText("Source:");
    await expect(dialog).not.toContainText("Ramp");
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
  });
}
