import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { installClearFixtures } from "./clear-fixtures";

const base = "/api/newsfeed/research/files/";
const first = base + "a".repeat(64) + ".png";
const second = base + "b".repeat(64) + ".png";
const source = {kind:"dropbox",publisher:"Morgan Stanley",url:base + "c".repeat(64) + ".pdf",documentDate:"2026-11-06",folderDate:"2026-11-07",pages:[2,3],figures:[{url:first,page:2,caption:"Exhibit 1: AI debt issuance"},{url:second,page:3,caption:"Exhibit 2: CDS AI basket vs CDX IG"}],fileId:"id:fixture",revision:"r1",contentHash:"c".repeat(64)};
const post = {id:"research-pdf",title:"Power and financing limit who can deliver AI capacity",content:"Financing is getting tighter too. AI debt issuance is running at 2.2x the 2023 rate.\n\nThe CDS AI basket trades >60bp wider than CDX IG.",timestamp:"2026-11-07T16:00:00Z",images:[first,second],tags:["MARKETDESK","DATA-CENTERS","AI","POWER","CREDIT"],source};
const chart = (label: string, stroke: string) => `<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600"><rect width="1000" height="600" fill="white"/><path d="M100 450L250 370L400 400L550 250L700 300L900 120" fill="none" stroke="${stroke}" stroke-width="6"/><text x="250" y="60" font-size="28">${label}</text></svg>`;

async function openFeed(page: Page) {
  await installClearFixtures(page);
  await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [post] }));
  await page.route("**/api/newsfeed/research/files/a*.png", route => route.fulfill({ contentType: "image/svg+xml", body: chart("AI debt issuance", "#137c5d") }));
  await page.route("**/api/newsfeed/research/files/b*.png", route => route.fulfill({ contentType: "image/svg+xml", body: chart("CDS basket", "#c2410c") }));
  await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
  return page.getByTestId("news-feed-item").filter({ hasText: post.title });
}

function countEmbeddedImages(pdf: string) { return pdf.match(/\/Subtype \/Image/g)?.length ?? 0; }

test("desktop downloads a Radon-branded PDF with every article chart", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const item = await openFeed(page);
  const actions = item.locator("[data-newsfeed-actions]");
  await expect(actions.getByRole("button", { name: "Export PDF" })).toBeVisible();
  await actions.screenshot({ path: testInfo.outputPath("pdf-actions-desktop.png") });
  const [download] = await Promise.all([page.waitForEvent("download"), actions.getByRole("button", { name: "Export PDF" }).click()]);
  expect(download.suggestedFilename()).toBe("radon-power-and-financing-limit-who-can-deliver-ai-capacity.pdf");
  const path = testInfo.outputPath("article.pdf");
  await download.saveAs(path);
  const pdf = (await readFile(path)).toString("latin1");
  expect(pdf.startsWith("%PDF-")).toBe(true);
  expect(countEmbeddedImages(pdf)).toBe(2);
  expect(pdf).toContain("/Title (Power and financing limit who can deliver AI capacity)");
  await expect(item.getByText("PDF downloaded.")).toBeVisible();
});

test.describe("installed app on a phone", () => {
  test.use({ viewport: { width: 393, height: 852 }, hasTouch: true, isMobile: true });

  test("hands the PDF to the share sheet for Save to Files", async ({ page }, testInfo) => {
    await page.addInitScript(() => {
      const shared: { name: string; type: string; head: string; size: number }[] = [];
      Object.assign(window, { __pdfShares: shared });
      Object.defineProperty(navigator, "canShare", { configurable: true, value: (data: { files?: File[] }) => Boolean(data.files?.length) });
      Object.defineProperty(navigator, "share", { configurable: true, value: async (data: { files: File[] }) => {
        const file = data.files[0];
        const bytes = new Uint8Array(await file.arrayBuffer());
        shared.push({ name: file.name, type: file.type, size: file.size, head: String.fromCharCode(...bytes.slice(0, 5)) });
      } });
    });
    const item = await openFeed(page);
    const button = item.getByRole("button", { name: "Export PDF" });
    await button.scrollIntoViewIfNeeded();
    await item.locator("[data-newsfeed-actions]").screenshot({ path: testInfo.outputPath("pdf-actions-mobile.png") });
    await button.click();
    await expect(item.getByText("PDF shared.")).toBeVisible();
    const shares = await page.evaluate(() => (window as unknown as { __pdfShares: unknown[] }).__pdfShares);
    expect(shares).toEqual([expect.objectContaining({ name: "radon-power-and-financing-limit-who-can-deliver-ai-capacity.pdf", type: "application/pdf", head: "%PDF-" })]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
});
