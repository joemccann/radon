import { test, expect, type Locator, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const RESEARCH_SOURCE = {
  kind: "dropbox",
  publisher: "JPM",
  url: `/api/newsfeed/research/files/${"a".repeat(64)}.pdf`,
  documentDate: "2026-09-01",
  folderDate: "2026-09-01",
  pages: [1],
  figures: [],
  fileId: "id",
  revision: "r1",
  contentHash: "c".repeat(64),
};

const AFTER_DIR = resolve(process.cwd(), "../docs/design-shots/live-market-footer/after");

function commentaryPost() {
  return {
    id: "p1",
    title: "Commentary fixture",
    content: "Body",
    timestamp: new Date().toISOString(),
    images: [] as string[],
    tags: ["MACRO"],
  };
}

async function fulfillFeed(page: Page, posts: object[]) {
  await page.route("**/api/newsfeed/posts**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(posts),
    }),
  );
  await page.route("**/api/newsfeed/research/held**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], pending: 0 }),
    }),
  );
}

async function textStartX(locator: Locator): Promise<number> {
  return locator.evaluate((el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    return range.getBoundingClientRect().x;
  });
}

test.describe("dashboard feed tabs", () => {
  test.beforeEach(async ({ page }) => {
    await fulfillFeed(page, [commentaryPost()]);
  });

  test("defaults to Commentary and never dials the upstream news host", async ({ page }) => {
    const leaked: string[] = [];
    page.on("websocket", (ws) => {
      if (/mktnews\.net/i.test(ws.url())) leaked.push(ws.url());
    });
    page.on("request", (req) => {
      if (/mktnews\.net/i.test(req.url())) leaked.push(req.url());
    });

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/dashboard");

    const tabs = page.getByRole("tab");
    await expect(tabs).toHaveText(["Commentary", "Headlines"]);
    await expect(page.getByRole("tab", { name: "Commentary" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText("Commentary fixture")).toBeVisible();

    await page.getByRole("tab", { name: "Headlines" }).click();
    await expect(page.getByRole("tab", { name: "Headlines" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("feed-panel-headlines")).toBeAttached();
    await expect(page.getByText(/Connecting to headlines|Waiting for headline prints|Headlines feed is unavailable/)).toBeVisible();
    expect(leaked).toEqual([]);
  });

  test("feed rail is a full-bleed single row at 1440 and 1024", async ({ page }) => {
    await fulfillFeed(page, [{ ...commentaryPost(), source: RESEARCH_SOURCE }]);
    for (const viewport of [
      { width: 1440, height: 900 },
      { width: 1024, height: 768 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/dashboard");
      const card = page.locator(".dashboard-news");
      const rail = page.getByTestId("feed-rail");
      await card.scrollIntoViewIfNeeded();
      await expect(rail).toBeVisible();

      const cardBox = (await card.boundingBox())!;
      const railBox = (await rail.boundingBox())!;
      expect(Math.abs(railBox.x - (cardBox.x + 1))).toBeLessThanOrEqual(1);
      expect(Math.abs(railBox.y + railBox.height - (cardBox.y + cardBox.height - 1))).toBeLessThanOrEqual(1);

      const itemBoxes = await Promise.all(
        ["source", "capture.basis", "last.sample"].map(async (key) =>
          (await rail.locator(`[data-k="${key}"]`).boundingBox())!,
        ),
      );
      expect(Math.abs(itemBoxes[0].y - itemBoxes[1].y)).toBeLessThanOrEqual(1);
      expect(Math.abs(itemBoxes[0].y - itemBoxes[2].y)).toBeLessThanOrEqual(1);

      const sourceValue = rail.locator('[data-k="source"] .v');
      expect((await sourceValue.boundingBox())!.x).toBeGreaterThanOrEqual(railBox.x + 12);

      const sourceX = await textStartX(rail.locator('[data-k="source"] .k'));
      const commentaryX = await textStartX(page.getByTestId("feed-tab-commentary"));
      expect(Math.abs(sourceX - commentaryX)).toBeLessThanOrEqual(1);

      const basisLabel = rail.locator('[data-k="capture.basis"] .k');
      await expect(basisLabel).toHaveCSS("white-space", "nowrap");
      expect(await basisLabel.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
      expect(itemBoxes[0].x + itemBoxes[0].width).toBeLessThanOrEqual(itemBoxes[1].x + 1);
      expect(itemBoxes[1].x + itemBoxes[1].width).toBeLessThanOrEqual(itemBoxes[2].x + 1);
      const gap1 = itemBoxes[1].x - (itemBoxes[0].x + itemBoxes[0].width);
      const gap2 = itemBoxes[2].x - (itemBoxes[1].x + itemBoxes[1].width);
      expect(gap1).toBeGreaterThan(0);
      expect(Math.abs(gap1 - gap2)).toBeLessThanOrEqual(2);
      const sourceValueBox = (await sourceValue.boundingBox())!;
      expect(itemBoxes[1].x - (sourceValueBox.x + sourceValueBox.width)).toBeLessThanOrEqual(24);
    }
  });

  test("1024 truncates a long source value instead of orphaning last.sample", async ({ page }) => {
    await fulfillFeed(page, [{ ...commentaryPost(), source: RESEARCH_SOURCE }]);
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.goto("/dashboard");
    const card = page.locator(".dashboard-news");
    await card.scrollIntoViewIfNeeded();
    await card.evaluate((el) => {
      el.style.width = "448px";
      el.style.maxWidth = "448px";
    });
    const sourceValue = page.locator('[data-testid="feed-rail"] [data-k="source"] .v');
    await expect(sourceValue).toHaveAttribute("title", "Market Ear + Research");
    const overflow = await sourceValue.evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(overflow).toBeGreaterThan(0);
    const itemBoxes = await Promise.all(
      ["source", "capture.basis", "last.sample"].map(async (key) =>
        (await page.locator(`[data-testid="feed-rail"] [data-k="${key}"]`).boundingBox())!,
      ),
    );
    expect(Math.abs(itemBoxes[0].y - itemBoxes[1].y)).toBeLessThanOrEqual(1);
    expect(Math.abs(itemBoxes[0].y - itemBoxes[2].y)).toBeLessThanOrEqual(1);
  });

  test("Held empty rail keeps Capture basis intact and stacks evenly on mobile", async ({ page }) => {
    await fulfillFeed(page, [{ ...commentaryPost(), source: RESEARCH_SOURCE }]);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/dashboard");
    const rail = page.getByTestId("feed-rail");
    await rail.scrollIntoViewIfNeeded();
    await page.getByTestId("feed-tab-held").click();
    await expect(page.getByText("Nothing held is waiting for review.")).toBeVisible();
    expect(await rail.locator('[data-k="last.sample"] .v').innerText()).toBe("---");

    const basisLabel = rail.locator('[data-k="capture.basis"] .k');
    await expect(basisLabel).toHaveText("Capture basis");
    await expect(basisLabel).toHaveCSS("white-space", "nowrap");
    expect(await basisLabel.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);

    const itemBoxes = await Promise.all(
      ["source", "capture.basis", "last.sample"].map(async (key) =>
        (await rail.locator(`[data-k="${key}"]`).boundingBox())!,
      ),
    );
    expect(Math.abs(itemBoxes[0].y - itemBoxes[1].y)).toBeLessThanOrEqual(1);
    expect(itemBoxes[0].x + itemBoxes[0].width).toBeLessThanOrEqual(itemBoxes[1].x + 1);
    expect(itemBoxes[1].x + itemBoxes[1].width).toBeLessThanOrEqual(itemBoxes[2].x + 1);
    const gap1 = itemBoxes[1].x - (itemBoxes[0].x + itemBoxes[0].width);
    const gap2 = itemBoxes[2].x - (itemBoxes[1].x + itemBoxes[1].width);
    expect(Math.abs(gap1 - gap2)).toBeLessThanOrEqual(2);
    const sourceValueBox = (await rail.locator('[data-k="source"] .v').boundingBox())!;
    expect(itemBoxes[1].x - (sourceValueBox.x + sourceValueBox.width)).toBeLessThanOrEqual(24);

    await page.setViewportSize({ width: 1024, height: 768 });
    await rail.scrollIntoViewIfNeeded();
    const midBoxes = await Promise.all(
      ["source", "capture.basis", "last.sample"].map(async (key) =>
        (await rail.locator(`[data-k="${key}"]`).boundingBox())!,
      ),
    );
    expect(Math.abs(midBoxes[0].y - midBoxes[1].y)).toBeLessThanOrEqual(1);
    expect(midBoxes[0].x + midBoxes[0].width).toBeLessThanOrEqual(midBoxes[1].x + 1);
    expect(midBoxes[1].x + midBoxes[1].width).toBeLessThanOrEqual(midBoxes[2].x + 1);
    await expect(basisLabel).toHaveCSS("white-space", "nowrap");

    await page.setViewportSize({ width: 393, height: 852 });
    await expect(page.locator("body")).toHaveAttribute("data-mobile", "true");
    const keys = rail.locator("[data-k] .k");
    const firstX = (await keys.nth(0).boundingBox())!.x;
    const secondX = (await keys.nth(1).boundingBox())!.x;
    const thirdX = (await keys.nth(2).boundingBox())!.x;
    expect(Math.abs(secondX - firstX)).toBeLessThanOrEqual(1);
    expect(Math.abs(thirdX - firstX)).toBeLessThanOrEqual(1);
    const itemYs = await Promise.all(
      [0, 1, 2].map(async (i) => (await rail.locator("[data-k]").nth(i).boundingBox())!.y),
    );
    expect(itemYs[1]).toBeGreaterThan(itemYs[0] + 4);
    expect(itemYs[2]).toBeGreaterThan(itemYs[1] + 4);
    const keyGaps = [itemYs[1] - itemYs[0], itemYs[2] - itemYs[1]];
    expect(Math.abs(keyGaps[0] - keyGaps[1])).toBeLessThanOrEqual(2);
    const overflow = await page.locator(".dashboard-news").evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    await expect(basisLabel).toHaveCSS("white-space", "nowrap");
    expect(await basisLabel.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
  });

  for (const theme of ["dark", "light"] as const) {
    test(`captures ${theme} after screenshot`, async ({ page }) => {
      await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
      await fulfillFeed(page, [{ ...commentaryPost(), source: RESEARCH_SOURCE }]);
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto("/dashboard");
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      const card = page.locator(".dashboard-news");
      await card.scrollIntoViewIfNeeded();
      await page.getByTestId("feed-tab-held").click();
      mkdirSync(AFTER_DIR, { recursive: true });
      await card.screenshot({
        path: `${AFTER_DIR}/desktop-${theme}.png`,
        animations: "disabled",
      });
    });
  }

  test("captures 1024 truncation and mobile stack after screenshots", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("theme", "dark"));
    await fulfillFeed(page, [{ ...commentaryPost(), source: RESEARCH_SOURCE }]);
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.goto("/dashboard");
    const card = page.locator(".dashboard-news");
    await card.scrollIntoViewIfNeeded();
    await card.evaluate((el) => {
      el.style.width = "448px";
      el.style.maxWidth = "448px";
    });
    mkdirSync(AFTER_DIR, { recursive: true });
    await card.screenshot({
      path: `${AFTER_DIR}/desktop-1024-truncation.png`,
      animations: "disabled",
    });

    await card.evaluate((el) => {
      el.style.width = "";
      el.style.maxWidth = "";
    });
    await page.setViewportSize({ width: 393, height: 852 });
    await expect(page.locator("body")).toHaveAttribute("data-mobile", "true");
    await card.scrollIntoViewIfNeeded();
    await card.screenshot({
      path: `${AFTER_DIR}/mobile-stack.png`,
      animations: "disabled",
    });
  });
});
