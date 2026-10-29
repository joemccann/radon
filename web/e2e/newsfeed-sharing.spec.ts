import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { installClearFixtures } from "./clear-fixtures";

const base = "/api/newsfeed/research/files/";
const first = base + "a".repeat(64) + ".png";
const second = base + "b".repeat(64) + ".png";
const source = {kind:"dropbox",publisher:"Synthetic Bank",url:base + "c".repeat(64) + ".pdf",documentDate:"2026-09-07",folderDate:"2026-09-07",pages:[2,3],figures:[{url:first,page:2,caption:"Positioning across sectors"},{url:second,page:3,caption:"Weekly distribution"}],fileId:"id:fixture",revision:"r1",contentHash:"c".repeat(64)};
const post = {id:"share-fixture",title:"Yen hedge demand increases",content:"Demand for yen hedges increased, while aggregate positioning remained near neutral. Source evidence does not establish a crowded short liquidation.",timestamp:"2026-09-07T16:00:00Z",images:[first,second],tags:["JPY","POSITIONING"],source};
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="600"><rect width="1000" height="600" fill="white"/><path d="M90 40V520H950" fill="none" stroke="#192b24" stroke-width="4"/><path d="M100 450L250 370L400 400L550 250L700 300L900 120" fill="none" stroke="#137c5d" stroke-width="6"/><text x="250" y="60" font-size="28">Synthetic chart: yen positioning</text></svg>`;

const excludedPublisher = /(?:the\s*)?market\s*ear|zero[\s-]*hedge/i;
type ShareCapture = { drawn: string[]; copied: string };

async function dismissNotices(page: Page) {
  // Persistent toasts are real interactive overlays. Acknowledge them using
  // their visible controls before continuing into the narrow mobile lightbox.
  const dismiss = page.locator(".toast-container").getByRole("button", { name: "Dismiss", exact: true });
  for (let count = 0; count < 16 && await dismiss.count(); count += 1) {
    await dismiss.first().click();
  }
  await expect(dismiss).toHaveCount(0);
}

test.beforeEach(async ({ page }) => {
  await installClearFixtures(page);
  await page.clock.setSystemTime(new Date());
  // A retired service must never be needed by Share, including on reopen.
  await page.route("**/api/newsfeed/share", route => route.fulfill({ status: 410, json: { error: "Retired" } }));
});

async function holdPreview(page: Page) {
  await page.addInitScript(() => {
    const toBlob = HTMLCanvasElement.prototype.toBlob;
    const pending: Array<() => void> = [];
    let released = false;
    Object.assign(window, { previewGate: {
      pending: false,
      release() { released = true; pending.splice(0).forEach(resume => resume()); },
    } });
    HTMLCanvasElement.prototype.toBlob = function (...args: Parameters<typeof toBlob>) {
      if (!released && this.width === 1080 && this.height === 1920) {
        (window as unknown as { previewGate: { pending: boolean } }).previewGate.pending = true;
        pending.push(() => toBlob.apply(this, args));
      } else toBlob.apply(this, args);
    };
  });
}
async function releasePreview(page: Page) {
  await page.evaluate(() => (window as unknown as { previewGate: { release: () => void } }).previewGate.release());
}

for (const width of [320, 393, 640, 1440]) {
  test(`original X copy is immediately editable while media is pending at ${width}px`, async ({ page, context }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await holdPreview(page);
    let rewrites = 0;
    page.on("request", request => { if (new URL(request.url()).pathname === "/api/newsfeed/share") rewrites++; });
    await page.addInitScript(() => Object.defineProperty(navigator, "clipboard", {
      configurable: true, value: { writeText: async (text: string) => Object.assign(window, { copiedCaption: text }) },
    }));
    await context.route(/^https:\/\/(?:twitter|x)\.com\/intent\/tweet\?/, route => route.fulfill({
      contentType: "text/html", body: "<title>Mock X composer</title><p>Compose draft</p>",
    }));
    await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [post] }));
    await page.route("**/api/newsfeed/research/files/*.png", route => route.fulfill({ contentType: "image/svg+xml", body: svg }));
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    const item = page.getByTestId("news-feed-item").filter({ hasText: post.title });
    const share = item.getByRole("button", { name: "Share", exact: true });
    const controls = [item.getByRole("button", { name: "Thumbs up", exact: true }), item.getByRole("button", { name: "Thumbs down", exact: true }), item.getByRole("button", { name: "Explain", exact: true }), share];
    async function assertHorizontal() {
      const boxes = await Promise.all(controls.map(control => control.boundingBox()));
      expect(boxes.every(Boolean)).toBe(true);
      expect(Math.max(...boxes.map(box => box!.y)) - Math.min(...boxes.map(box => box!.y))).toBeLessThanOrEqual(1);
      for (let i = 1; i < boxes.length; i++) expect(boxes[i]!.x).toBeGreaterThanOrEqual(boxes[i - 1]!.x + boxes[i - 1]!.width);
    }
    await assertHorizontal();
    await share.click();
    const panel = item.getByRole("region", { name: "Share news item" });
    const caption = panel.getByRole("textbox", { name: "Post caption" });
    const compose = panel.getByRole("link", { name: "Compose on X" });
    const expected = `${post.title}\n\nDemand for yen hedges increased, while aggregate positioning remained near neutral.\n\nSource evidence does not establish a crowded short liquidation.`;
    await expect(caption).toBeEnabled();
    await expect(caption).toHaveValue(expected);
    await expect(panel.getByRole("button", { name: "Copy caption", exact: true })).toBeEnabled();
    await expect(panel.getByRole("progressbar")).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => (window as unknown as { previewGate: { pending: boolean } }).previewGate.pending)).toBe(true);
    await expect(panel.getByRole("button", { name: "Download Story image" })).toBeDisabled();
    await assertHorizontal();
    await caption.fill("Yen hedge demand increased. Positioning remained near neutral.");
    await panel.getByRole("button", { name: "Copy caption", exact: true }).click();
    await expect.poll(() => page.evaluate(() => (window as unknown as { copiedCaption: string }).copiedCaption)).toBe(await caption.inputValue());
    const popupReady = page.waitForEvent("popup");
    await compose.click();
    const popup = await popupReady;
    await expect(popup).toHaveTitle("Mock X composer");
    expect(new URL(popup.url()).searchParams.get("text")).toBe(await caption.inputValue());
    await popup.close();
    await dismissNotices(page);
    await item.screenshot({ path: testInfo.outputPath(`horizontal-share-pending-${width}.png`) });
    await releasePreview(page);
    await expect(panel.getByRole("button", { name: "Download Story image" })).toBeEnabled();
    await share.click();
    await share.click();
    await expect(caption).toHaveValue(expected);
    await assertHorizontal();
    expect(rewrites).toBe(0);
  });
}

test("image failure leaves original X composition and copying available", async ({ page }) => {
  await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [post] }));
  await page.route("**/api/newsfeed/research/files/*.png", route => route.fulfill({ status: 404, body: "Missing fixture chart" }));
  await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
  const item = page.getByTestId("news-feed-item").filter({ hasText: post.title });
  await item.getByRole("button", { name: "Share", exact: true }).click();
  const panel = item.getByRole("region", { name: "Share news item" });
  await expect(page.locator(".toast-container").getByRole("alert").filter({ hasText: "chart could not be loaded" })).toBeVisible();
  const caption = panel.getByRole("textbox", { name: "Post caption" });
  await expect(caption).toBeEnabled();
  await caption.fill("Yen hedge demand increased.");
  expect(new URL((await panel.getByRole("link", { name: "Compose on X" }).getAttribute("href"))!).searchParams.get("text")).toBe("Yen hedge demand increased.");
  await expect(panel.getByRole("button", { name: "Copy caption", exact: true })).toBeEnabled();
  await expect(panel.getByRole("button", { name: "Download Story image" })).toBeDisabled();
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
    const originalCaption = await panel.getByRole("textbox", { name: "Post caption" }).inputValue();
    expect(originalCaption).toContain(post.title);
    expect(originalCaption).toContain("Source evidence does not establish a crowded short liquidation.");
    expect(originalCaption).not.toMatch(/Source:|@Synthetic/);
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
    await expect(dialog.locator(".newsfeed-lightbox__image")).toHaveJSProperty("src", new URL(second, page.url()).href);
    await dismissNotices(page);
    await dialog.getByRole("button", { name: "Share", exact: true }).click();
    const lightboxPanel = dialog.getByRole("region", { name: "Share news item" });
    await expect(lightboxPanel.getByRole("img")).toBeVisible({ timeout: 30_000 });
    await lightboxPanel.screenshot({ path: testInfo.outputPath(`news-lightbox-share-${width}.png`) });
    const caption = lightboxPanel.getByRole("textbox", { name: "Post caption" });
    await caption.fill("Edited caption in the lightbox");
    await caption.press("ArrowRight");
    await caption.press("ArrowLeft");
    await expect(dialog.getByRole("heading", { name: post.title })).toBeVisible();
    await expect(dialog.locator(".newsfeed-lightbox__image")).toHaveJSProperty("src", new URL(second, page.url()).href);
    await caption.press("Escape");
    await expect(lightboxPanel).toHaveCount(0);
    await expect(dialog).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
  });
}

for (const width of [1440, 393]) {
  test(`X sharing retains the complete Morgan Stanley post without rewrite and on reopen at ${width}px`, async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    const fixture = {
      ...post,
      id: "morgan-stanley-ai-cds",
      title: "Morgan Stanley: AI CDS basket trades ~45bp wider than CDX IG, and protection is still its preferred credit-derivatives expression",
      content: 'Morgan Stanley\'s AI CDS basket now trades ~45bp wider than CDX IG.\n\nMS says buying CDS protection "remains our preferred way of playing the AI story in the credit derivatives market." The reason is not just a bearish default call. MS expects "increasing needs for non-economic hedging to manage counterparty exposure," even in a benign scenario where AI investments are profitable and continue.\n\nThat is an important distinction. The hedge bid can grow even if the capex cycle keeps working. More AI financing creates more counterparty exposure, and that creates more demand for protection.',
      source: { ...source, publisher: "Tyler Durden", documentDate: "2026-10-07" },
    };
    const expected = `${fixture.title}\n\n${fixture.content}`.replace(/Morgan Stanley|\bMS\b/g, "@morganstanley");
    let requests = 0;
    await page.setViewportSize({ width, height: 1000 });
    await page.addInitScript(() => {
      const capture: ShareCapture = { drawn: [], copied: "" };
      Object.assign(window, { shareCapture: capture });
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: { writeText: async (text: string) => { capture.copied = text; } },
      });
    });
    page.on("request", request => { if (new URL(request.url()).pathname === "/api/newsfeed/share") requests++; });
    await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [fixture] }));
    await page.route("**/api/newsfeed/research/files/*.png", route => route.fulfill({ contentType: "image/svg+xml", body: svg }));
    try {
      await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
      const item = page.getByTestId("news-feed-item").filter({ hasText: fixture.title });
      const share = item.getByRole("button", { name: "Share", exact: true });
      await share.click();
      const panel = item.getByRole("region", { name: "Share news item" });
      const caption = panel.getByRole("textbox", { name: "Post caption" });
      const compose = panel.getByRole("link", { name: "Compose on X" });
      expect(expected.length).toBeGreaterThan(400);
      await expect(caption).toBeEnabled();
      await expect(caption).toHaveValue(expected);
      await panel.getByRole("button", { name: "Copy caption", exact: true }).click();
      await expect.poll(() => page.evaluate(() => (window as unknown as { shareCapture: ShareCapture }).shareCapture.copied)).toBe(expected);
      await panel.screenshot({ path: testInfo.outputPath(`morgan-stanley-full-x-share-${width}.png`) });
      await share.click();
      await share.click();
      await expect(caption).toHaveValue(expected);
      expect(new URL((await compose.getAttribute("href"))!).searchParams.get("text")).toBe(expected);
      expect(requests).toBe(0);
      expect(expected).not.toContain("Source:");
      expect(expected).not.toContain("Tyler Durden");
      expect(expected).toContain("and that creates more demand for protection.");
    } finally {
      await page.unrouteAll({ behavior: "wait" });
    }
  });
}

for (const width of [320, 393, 640, 1440]) {
  for (const surface of ["feed", "lightbox"] as const) {
    test(`share panel stays contained while pending and ready in ${surface} at ${width}px`, async ({ page }, testInfo) => {
      test.setTimeout(90_000);
      await page.setViewportSize({ width, height: 1000 });
      if (width === 393) await page.addInitScript(() => localStorage.setItem("theme", "dark"));
      await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [post] }));
      await page.route("**/api/newsfeed/research/files/*.png", route => route.fulfill({ contentType: "image/svg+xml", body: svg }));
      await holdPreview(page);
      try {
        await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
        const item = page.getByTestId("news-feed-item").filter({ hasText: post.title });
        let container = item;
        if (surface === "lightbox") {
          await item.locator(".news-feed-image-wrapper").first().click();
          container = page.getByRole("dialog");
        }
        await container.getByRole("button", { name: "Share", exact: true }).click();
        const panel = container.getByRole("region", { name: "Share news item" });
        const caption = panel.getByRole("textbox", { name: "Post caption" });
        async function assertContained(state: string) {
          const notices = page.getByRole("button", { name: "Dismiss", exact: true });
          for (let count = 0; count < 16 && await notices.count(); count += 1) await notices.first().click();
          await panel.screenshot({ path: testInfo.outputPath(`share-${surface}-${width}-${state}.png`) });
          const geometry = await panel.evaluate(el => {
            const box = el.getBoundingClientRect();
            const parent = el.closest(".news-feed-item, .newsfeed-lightbox__copy") ?? el.parentElement!.parentElement!;
            const parentBox = parent.getBoundingClientRect();
            const controls = [...el.querySelectorAll("button, a, textarea, img, [role=progressbar]")].map(node => {
              const rect = node.getBoundingClientRect();
              return { text: node.textContent || node.getAttribute("aria-label") || node.tagName, left: rect.left, right: rect.right };
            });
            return { left: box.left, right: box.right, parentLeft: parentBox.left, parentRight: parentBox.right,
              client: el.clientWidth, scroll: el.scrollWidth, viewport: document.documentElement.clientWidth,
              pageScroll: document.documentElement.scrollWidth, controls };
          });
          expect(geometry.left).toBeGreaterThanOrEqual(Math.max(0, geometry.parentLeft) - 1);
          expect(geometry.right).toBeLessThanOrEqual(Math.min(geometry.viewport, geometry.parentRight) + 1);
          expect(geometry.scroll).toBeLessThanOrEqual(geometry.client + 1);
          expect(geometry.pageScroll).toBeLessThanOrEqual(geometry.viewport + 1);
          for (const control of geometry.controls) {
            expect(control.left, control.text).toBeGreaterThanOrEqual(geometry.left - 1);
            expect(control.right, control.text).toBeLessThanOrEqual(geometry.right + 1);
          }
        }
        await expect(panel.getByRole("textbox", { name: "Post caption" })).toBeEnabled();
        await expect(panel.getByRole("button", { name: "Download Story image" })).toBeDisabled();
        await assertContained("pending");
        await releasePreview(page);
        await expect(panel.getByRole("button", { name: "Download Story image" })).toBeEnabled();
        const longCaption = `${post.title}\n\nhttps://example.invalid/${"research".repeat(40)}`;
        await caption.fill(longCaption);
        await expect(caption).toHaveValue(longCaption);
        await assertContained("ready");
        const download = page.waitForEvent("download");
        await panel.getByRole("button", { name: "Download Story image" }).click();
        expect((await download).suggestedFilename()).toMatch(/\.png$/);
        await container.getByRole("button", { name: "Share", exact: true }).click();
        await expect(panel).toHaveCount(0);
      } finally {
        await releasePreview(page);
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    });
  }
}
