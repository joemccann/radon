import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

const base = "/api/newsfeed/research/files/";
const first = base + "a".repeat(64) + ".png";
const second = base + "b".repeat(64) + ".png";
const source = {kind:"dropbox",publisher:"Synthetic Bank",url:base + "c".repeat(64) + ".pdf",documentDate:"2026-09-07",folderDate:"2026-09-07",pages:[2,3],figures:[{url:first,page:2,caption:"Positioning across sectors"},{url:second,page:3,caption:"Weekly distribution"}],fileId:"id:fixture",revision:"r1",contentHash:"c".repeat(64)};
const post = {id:"share-fixture",title:"Yen hedge demand increases",content:"Demand for yen hedges increased, while aggregate positioning remained near neutral. Source evidence does not establish a crowded short liquidation.",timestamp:"2026-09-07T16:00:00Z",images:[first,second],tags:["JPY","POSITIONING"],source};
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600"><rect width="1000" height="600" fill="white"/><path d="M90 40V520H950" fill="none" stroke="#192b24" stroke-width="4"/><path d="M100 450L250 370L400 400L550 250L700 300L900 120" fill="none" stroke="#137c5d" stroke-width="6"/><text x="250" y="60" font-size="28">Synthetic chart: yen positioning</text></svg>`;

const excludedPublisher = /(?:the\s*)?market\s*ear|zero[\s-]*hedge/i;
type ShareCapture = { drawn: string[]; copied: string };

test.beforeEach(async ({ page }) => {
  await page.route("**/api/newsfeed/share", async route => {
    const request = route.request().postDataJSON() as { title: string; content: string };
    await route.fulfill({ json: {
      title: request.title, content: request.content,
      caption: [request.title, request.content].join("\n\n"),
    } });
  });
});

for (const width of [1440, 393]) {
  test(`news sharing composes on X while voice and preview are pending at ${width}px`, async ({ page, context }, testInfo) => {
    test.setTimeout(90_000);
    const fixture = {
      ...post,
      title: "The Market Ear: Yen hedge demand — increases",
      content: `${post.content} Source: ZeroHedge https://zerohedge.com/markets/example`,
    };
    const rewritten = { title: "Seasonality — the setup", content: "Positioning &mdash; the tell. Returns: -2.5%." };
    const rewrittenCaption = "Seasonality, the setup\n\nPositioning, the tell. Returns: -2.5%.";
    let requests = 0;
    let releaseRewrite!: () => void;
    const rewriteReady = new Promise<void>(resolve => { releaseRewrite = resolve; });
    await page.route("**/api/newsfeed/share", async route => {
      requests += 1;
      await rewriteReady;
      await route.fulfill({ json: { ...rewritten, caption: rewrittenCaption } });
    });
    // Context routing also catches the first navigation in target="_blank" pages.
    await context.route(/^https:\/\/(?:twitter|x)\.com\/intent\/tweet\?/, route => route.fulfill({
      contentType: "text/html", body: "<title>Mock X composer</title><p>Compose draft</p>",
    }));
    await page.setViewportSize({ width, height: 1000 });
    await page.addInitScript(() => {
      const capture: ShareCapture = { drawn: [], copied: "" };
      Object.assign(window, { shareCapture: capture });
      const fillText = CanvasRenderingContext2D.prototype.fillText;
      CanvasRenderingContext2D.prototype.fillText = function (...args: Parameters<typeof fillText>) {
        capture.drawn.push(args[0]);
        return fillText.apply(this, args);
      };
      const toBlob = HTMLCanvasElement.prototype.toBlob;
      let released = false;
      const pending: Array<() => void> = [];
      const previewGate = {
        pending: false,
        release() {
          released = true;
          pending.splice(0).forEach(resume => resume());
        },
      };
      Object.assign(window, { previewGate });
      HTMLCanvasElement.prototype.toBlob = function (...args: Parameters<typeof toBlob>) {
        if (!released && this.width === 1080 && this.height === 1920) {
          previewGate.pending = true;
          pending.push(() => toBlob.apply(this, args));
          return;
        }
        return toBlob.apply(this, args);
      };
    });
    await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [fixture] }));
    await page.route("**/api/newsfeed/research/files/*.png", route => route.fulfill({ contentType: "image/svg+xml", body: svg }));
    try {
      await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
      const item = page.getByTestId("news-feed-item").filter({ hasText: "Yen hedge demand" });
      await item.getByRole("button", { name: "Share", exact: true }).click();
      const panel = item.getByRole("region", { name: "Share news item" });
      const caption = panel.getByRole("textbox", { name: "Post caption" });
      const compose = panel.getByRole("link", { name: "Compose on X" });
      async function openComposer(expectedText: string) {
        await expect(compose).toBeEnabled();
        const popupReady = page.waitForEvent("popup");
        await compose.click();
        const popup = await popupReady;
        try {
          await expect(popup).toHaveTitle("Mock X composer");
          expect(new URL(popup.url()).searchParams.get("text")).toBe(expectedText);
        } finally { await popup.close(); }
      }
      await expect(panel.getByRole("status")).toHaveText("Writing in your voice…");
      await expect(caption).toBeDisabled();
      await expect(panel.getByText("Preparing preview…", { exact: true })).toBeVisible();
      await expect(panel.getByRole("button", { name: "Download Story image" })).toBeDisabled();
      await expect(panel.getByRole("button", { name: "Download Reels / TikTok video" })).toBeDisabled();
      const fallbackCaption = await caption.inputValue();
      expect(fallbackCaption).toContain("Yen hedge demand, increases");
      expect(fallbackCaption).toContain(post.content);
      expect(fallbackCaption).not.toMatch(excludedPublisher);
      expect(fallbackCaption).not.toMatch(/—|&(?:mdash|#8212|#x2014);/i);
      await openComposer(fallbackCaption);
      await panel.screenshot({ path: testInfo.outputPath(`share-compose-during-rewrite-${width}.png`) });

      releaseRewrite();
      await expect(caption).toHaveValue(rewrittenCaption);
      await expect.poll(() => page.evaluate(() => (window as unknown as { previewGate: { pending: boolean } }).previewGate.pending), { timeout: 30_000 }).toBe(true);
      await expect(caption).toBeEnabled();
      await expect(panel.getByText("Preparing preview…", { exact: true })).toBeVisible();
      await expect(panel.getByRole("button", { name: "Download Story image" })).toBeDisabled();
      await expect(panel.getByRole("button", { name: "Download Reels / TikTok video" })).toBeDisabled();
      await openComposer(rewrittenCaption);
      await panel.screenshot({ path: testInfo.outputPath(`share-compose-during-preview-${width}.png`) });
      await page.evaluate(() => (window as unknown as { previewGate: { release: () => void } }).previewGate.release());
      await expect(panel.getByRole("button", { name: "Download Story image" })).toBeEnabled();
      expect(new URL((await compose.getAttribute("href"))!).searchParams.get("text")).toBe(rewrittenCaption);
      const drawn = await page.evaluate(() => (window as unknown as { shareCapture: ShareCapture }).shareCapture.drawn.join(" "));
      expect(drawn).toContain("Seasonality, the setup");
      expect(drawn).toContain("Positioning, the tell. Returns: -2.5%.");
      expect(drawn).not.toMatch(/—|&(?:mdash|#8212|#x2014);/i);
      expect(requests).toBe(1);
      await panel.screenshot({ path: testInfo.outputPath("rewritten-share.png") });
      const download = page.waitForEvent("download");
      await panel.getByRole("button", { name: "Download Story image" }).click();
      await (await download).saveAs(testInfo.outputPath("rewritten-share-card.png"));
    } finally {
      releaseRewrite();
      if (!page.isClosed()) {
        await page.evaluate(() => (window as unknown as { previewGate?: { release: () => void } }).previewGate?.release());
      }
      await page.unrouteAll({ behavior: "wait" });
    }
  });
}

test("news sharing retains sanitized fallback and retries a failed voice rewrite", async ({ page }) => {
  test.setTimeout(90_000);
  let requests = 0;
  await page.route("**/api/newsfeed/share", async route => {
    requests += 1;
    if (requests === 1) return route.fulfill({ status: 503, json: { error: "Rewrite unavailable" } });
    await route.fulfill({ json: { title: "Retry succeeds", content: "Positioning remains the tell.", caption: "Retry succeeds\n\nPositioning remains the tell." } });
  });
  await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [{ ...post, source: undefined }] }));
  await page.route("**/api/newsfeed/research/files/*.png", route => route.fulfill({ contentType: "image/svg+xml", body: svg }));
  await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
  const item = page.getByTestId("news-feed-item").filter({ hasText: post.title });
  await item.getByRole("button", { name: "Share", exact: true }).click();
  const panel = item.getByRole("region", { name: "Share news item" });
  await expect(panel.getByRole("button", { name: "Retry voice rewrite" })).toBeVisible();
  expect(await panel.getByRole("textbox", { name: "Post caption" }).inputValue()).toContain(post.title);
  expect(await panel.getByRole("textbox", { name: "Post caption" }).inputValue()).not.toMatch(excludedPublisher);
  await expect(panel.getByRole("button", { name: "Download Story image" })).toBeEnabled();
  await panel.getByRole("button", { name: "Retry voice rewrite" }).click();
  await expect(panel.getByRole("textbox", { name: "Post caption" })).toHaveValue("Retry succeeds\n\nPositioning remains the tell.");
  expect(requests).toBe(2);
});

for (const width of [1440, 393]) {
  for (const publisher of ["The Market Ear", "ZeroHedge"]) {
    test(`news sharing excludes ${publisher} from captions and exported text at ${width}px`, async ({ page }, testInfo) => {
      test.setTimeout(90_000);
      const fixture = {
        ...post,
        title: `${publisher}: Midterm seasonality points higher`,
        content: "The Market Ear reports seasonality points higher. ZeroHedge sees positioning improve. https://themarketear.com/posts/example https://www.zerohedge.com/markets/example",
        source: publisher === "The Market Ear" ? undefined : {
          ...source, publisher,
          figures: [{ url: first, page: 2, caption: "ZeroHedge: Positioning improves" }],
        },
      };
      await page.setViewportSize({ width, height: 1000 });
      await page.addInitScript(() => {
        const capture: ShareCapture = { drawn: [], copied: "" };
        Object.assign(window, { shareCapture: capture });
        const fillText = CanvasRenderingContext2D.prototype.fillText;
        CanvasRenderingContext2D.prototype.fillText = function (...args: Parameters<typeof fillText>) {
          capture.drawn.push(args[0]);
          return fillText.apply(this, args);
        };
        Object.defineProperty(navigator, "clipboard", {
          configurable: true,
          value: { writeText: async (text: string) => { capture.copied = text; } },
        });
      });
      await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [fixture] }));
      await page.route("**/api/newsfeed/research/files/*.png", route => route.fulfill({ contentType: "image/svg+xml", body: svg }));
      await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
      const item = page.getByTestId("news-feed-item").filter({ hasText: fixture.title });
      await item.getByRole("button", { name: "Share", exact: true }).click();
      const panel = item.getByRole("region", { name: "Share news item" });
      await expect(panel.getByRole("img")).toBeVisible({ timeout: 30_000 });
      const caption = panel.getByRole("textbox", { name: "Post caption" });
      expect(await caption.inputValue()).not.toMatch(excludedPublisher);
      expect(await caption.inputValue()).toContain("seasonality points higher");
      const drawn = await page.evaluate(() => (window as unknown as { shareCapture: ShareCapture }).shareCapture.drawn.join(" "));
      expect(drawn).toContain("RADON");
      expect(drawn).toContain("seasonality points higher");
      expect(drawn).not.toMatch(excludedPublisher);
      const compose = panel.getByRole("link", { name: "Compose on X" });
      expect(new URL((await compose.getAttribute("href"))!).searchParams.get("text")).not.toMatch(excludedPublisher);
      await caption.fill("Seasonality — still improving. Range: 10&#8212;20%. Source: The Market Ear https://themarketear.com/posts/example ZeroHedge https://zerohedge.com/markets/example");
      const outbound = new URL((await compose.getAttribute("href"))!).searchParams.get("text");
      expect(outbound).toContain("Seasonality, still improving. Range: 10 to 20%.");
      expect(outbound).not.toMatch(excludedPublisher);
      expect(outbound).not.toMatch(/—|&(?:mdash|#8212|#x2014);/i);
      await panel.getByRole("button", { name: "Copy caption", exact: true }).click();
      await expect(panel.getByRole("status")).toHaveText("Caption copied.");
      const copied = await page.evaluate(() => (window as unknown as { shareCapture: ShareCapture }).shareCapture.copied);
      expect(copied).toBe(outbound);
      expect(copied).not.toMatch(excludedPublisher);
      expect(copied).not.toMatch(/—|&(?:mdash|#8212|#x2014);/i);
      await caption.fill("Seasonality improves.");
      await panel.screenshot({ path: testInfo.outputPath(`publisher-free-share-${width}.png`) });
      await panel.getByRole("img").screenshot({ path: testInfo.outputPath(`publisher-free-card-${width}.png`) });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
  }
}

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
    await expect(panel.getByRole("textbox", { name: "Post caption" })).toHaveValue(/Source: Synthetic Bank/);
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
