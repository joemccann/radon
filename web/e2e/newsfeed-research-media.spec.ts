import { test, expect } from "@playwright/test";
const base = "/api/newsfeed/research/files/";
const first = base + "a".repeat(64) + ".png";
const second = base + "b".repeat(64) + ".png";
const pdf = base + "c".repeat(64) + ".pdf";
const source = {kind:"dropbox",publisher:"Synthetic Bank",url:pdf,documentDate:"2026-09-07",folderDate:"2026-09-07",pages:[2,3],figures:[{url:first,page:2,caption:"Positioning across sectors"},{url:second,page:3,caption:"Weekly distribution"}],fileId:"id:fixture",revision:"r1",contentHash:"c".repeat(64)};
const post = {id:"research-fixture",title:"New positioning evidence",content:"Synthetic source evidence for the private research media rendering test.",timestamp:"2026-09-07T16:00:00Z",images:[first,second],tags:["POSITIONING"],source};
const svg = (width:number,height:number) => `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 1000 600"><rect width="1000" height="600" fill="white"/><path d="M90 40V520H950" fill="none" stroke="#192b24" stroke-width="4"/><path d="M100 450L250 370L400 400L550 250L700 300L900 120" fill="none" stroke="#137c5d" stroke-width="6"/><text x="25" y="50" font-size="24">100</text><text x="30" y="520" font-size="24">0</text><text x="90" y="565" font-size="24">Jan</text><text x="900" y="565" font-size="24">Sep</text><text x="400" y="60" font-size="28">Synthetic chart</text></svg>`;
for (const width of [1440,393]) {
 test(`private research charts preserve source context at ${width}px`, async ({page}, testInfo) => {
   await page.setViewportSize({width,height:900});
   const optimizerRequests:string[]=[];
   page.on("request", request => { if(request.url().includes("/_next/image") && request.url().includes("research")) optimizerRequests.push(request.url()); });
   await page.route("**/api/newsfeed/posts**", route => route.fulfill({json:[post,{...post,id:"research-text",title:"Text evidence only",images:[],source:{...source,figures:[]}}]}));
   await page.route("**/api/newsfeed/research/files/*.png", route => route.fulfill({contentType:"image/svg+xml",body:svg(1000,600)}));
   await page.goto("/dashboard", {waitUntil:"domcontentloaded"});
   const item=page.getByTestId("news-feed-item").filter({hasText:post.title});
   await expect(item.getByText("Synthetic Bank · p. 2 · Positioning across sectors")).toBeVisible();
   await expect(item.getByRole("link",{name:"Synthetic Bank · Source PDF"})).toHaveAttribute("href",pdf);
   await expect(page.getByText(/Text-only source evidence/)).toBeVisible();
   const image=item.locator(".news-feed-image");
   await expect(image).toHaveJSProperty("naturalWidth",1000);
   expect(await image.evaluate(el=> Math.abs(el.getBoundingClientRect().width / el.getBoundingClientRect().height - 1000/600))).toBeLessThan(0.03);
   expect(await item.locator("figcaption span").evaluate(el => getComputedStyle(el).whiteSpace)).toBe("normal");
   await item.screenshot({path:testInfo.outputPath(`research-feed-${width}.png`)});
   await item.getByRole("button",{name:"Open chart 2: Weekly distribution"}).click();
   await expect(page.locator(".newsfeed-lightbox__image")).toHaveAttribute("src",second);
   await expect(page.getByRole("dialog").getByText("Synthetic Bank · p. 3 · Weekly distribution")).toBeVisible();
   await page.getByRole("button",{name:"View chart 1: Positioning across sectors"}).click();
   await expect(page.locator(".newsfeed-lightbox__image")).toHaveAttribute("src",first);
   await page.screenshot({path:testInfo.outputPath(`research-lightbox-${width}.png`)});
   expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
   expect(optimizerRequests).toEqual([]);
   await page.keyboard.press("Escape");
   await expect(page.getByRole("dialog")).toHaveCount(0);
 });
}
