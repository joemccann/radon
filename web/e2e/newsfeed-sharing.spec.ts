import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

const base = "/api/newsfeed/research/files/";
const first = base + "a".repeat(64) + ".png";
const second = base + "b".repeat(64) + ".png";
const source = {kind:"dropbox",publisher:"Synthetic Bank",url:base + "c".repeat(64) + ".pdf",documentDate:"2026-09-07",folderDate:"2026-09-07",pages:[2,3],figures:[{url:first,page:2,caption:"Positioning across sectors"},{url:second,page:3,caption:"Weekly distribution"}],fileId:"id:fixture",revision:"r1",contentHash:"c".repeat(64)};
const post = {id:"share-fixture",title:"Yen hedge demand increases",content:"Demand for yen hedges increased, while aggregate positioning remained near neutral. Source evidence does not establish a crowded short liquidation.",timestamp:"2026-09-07T16:00:00Z",images:[first,second],tags:["JPY","POSITIONING"],source};
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600"><rect width="1000" height="600" fill="white"/><path d="M90 40V520H950" fill="none" stroke="#192b24" stroke-width="4"/><path d="M100 450L250 370L400 400L550 250L700 300L900 120" fill="none" stroke="#137c5d" stroke-width="6"/><text x="250" y="60" font-size="28">Synthetic chart: yen positioning</text></svg>`;

for (const width of [1440, 393]) {
  test(`news sharing exports portrait media and preserves caption navigation at ${width}px`, async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 1000 });
    await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [post, { ...post, id: "another-item", title: "Other analysis" }] }));
    await page.route("**/api/newsfeed/research/files/*.png", route => route.fulfill({ contentType: "image/svg+xml", body: svg }));
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    const item = page.getByTestId("news-feed-item").filter({ hasText: post.title });
    await item.getByRole("button", { name: "Share", exact: true }).click();
    const panel = item.getByRole("region", { name: "Share news item" });
    const preview = panel.getByRole("img", { name: `Portrait share preview: ${post.title}` });
    await expect(preview).toBeVisible({ timeout: 30_000 });
    await expect(preview).toHaveJSProperty("naturalWidth", 1080);
    await expect(preview).toHaveJSProperty("naturalHeight", 1920);
    await panel.getByRole("textbox", { name: "Post caption" }).fill("Edited yen analysis & source attribution");
    const compose = panel.getByRole("link", { name: "Compose on X" });
    expect(new URL((await compose.getAttribute("href"))!).searchParams.get("text")).toBe("Edited yen analysis & source attribution");
    const pngDownload = page.waitForEvent("download");
    await panel.getByRole("button", { name: "Download Story image" }).click();
    const png = await pngDownload;
    await png.saveAs(testInfo.outputPath(`news-share-export-${width}.png`));
    expect(png.suggestedFilename()).toMatch(/\.png$/);
    const bytes = await readFile((await png.path())!);
    expect(bytes.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    expect(bytes.readUInt32BE(16)).toBe(1080);
    expect(bytes.readUInt32BE(20)).toBe(1920);
    await preview.screenshot({ path: testInfo.outputPath(`news-share-preview-${width}.png`) });
    await panel.screenshot({ path: testInfo.outputPath(`news-share-${width}.png`) });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

    if (width === 1440) {
      const videoButton = panel.getByRole("button", { name: "Download Reels / TikTok video" });
      if (await videoButton.isEnabled()) {
        const videoDownload = page.waitForEvent("download", { timeout: 30_000 });
        await videoButton.click();
        const video = await videoDownload;
        await video.saveAs(testInfo.outputPath("news-share-export.mp4"));
        expect(video.suggestedFilename()).toMatch(/\.mp4$/);
        const videoBytes = await readFile((await video.path())!);
        expect(videoBytes.subarray(4, 8).toString()).toBe("ftyp");
        const decodePage = await page.context().newPage();
        const metadata = await decodePage.evaluate(async base64 => {
          const bytes = Uint8Array.from(atob(base64), character => character.charCodeAt(0));
          const url = URL.createObjectURL(new Blob([bytes], { type: "video/mp4" }));
          try {
            const video = document.createElement("video");
            video.src = url;
            await new Promise<void>((resolve, reject) => { video.onloadedmetadata = () => resolve(); video.onerror = () => reject(new Error("Exported MP4 cannot be decoded")); });
            return { width: video.videoWidth, height: video.videoHeight, duration: video.duration };
          } finally { URL.revokeObjectURL(url); }
        }, videoBytes.toString("base64"));
        await decodePage.close();
        expect(metadata.width).toBe(1080);
        expect(metadata.height).toBe(1920);
        expect(metadata.duration).toBeGreaterThanOrEqual(5);
        expect(metadata.duration).toBeLessThan(15);
      } else {
        await expect(panel.getByText(/MP4 export is unavailable/)).toBeVisible();
      }
    }

    await item.getByRole("button", { name: "Share", exact: true }).click();
    await item.getByRole("button", { name: "Open chart 2: Weekly distribution" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.locator(".newsfeed-lightbox__image")).toHaveAttribute("src", second);
    await dialog.getByRole("button", { name: "Share", exact: true }).click();
    const lightboxPanel = dialog.getByRole("region", { name: "Share news item" });
    await expect(lightboxPanel.getByRole("img")).toBeVisible({ timeout: 30_000 });
    await lightboxPanel.screenshot({ path: testInfo.outputPath(`news-lightbox-share-${width}.png`) });
    const caption = lightboxPanel.getByRole("textbox", { name: "Post caption" });
    await caption.fill("Edited caption in the lightbox");
    await caption.press("ArrowRight");
    await caption.press("ArrowLeft");
    await expect(dialog.getByRole("heading", { name: post.title })).toBeVisible();
    await expect(dialog.locator(".newsfeed-lightbox__image")).toHaveAttribute("src", second);
    await caption.press("Escape");
    await expect(lightboxPanel).toHaveCount(0);
    await expect(dialog).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
  });
}
