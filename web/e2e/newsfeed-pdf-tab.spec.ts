import { test, expect } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";

const pdfUrl = `/api/newsfeed/research/files/${"a".repeat(64)}.pdf`;
const chartUrl = `/api/newsfeed/research/files/${"b".repeat(64)}.png`;
const source = {
  kind: "dropbox", publisher: "TS Lombard", url: pdfUrl,
  documentDate: "2026-10-16", folderDate: "2026-10-16", pages: [1],
  figures: [{ url: chartUrl, page: 1, caption: "Research positioning" }],
  fileId: "id:fixture", revision: "r1", contentHash: "a".repeat(64),
};
const ordinary = {
  id: "commentary", title: "Ordinary market commentary", content: "Not an imported PDF article.",
  timestamp: "2026-10-16T14:00:00Z", images: ["/fixture-commentary.svg"], tags: ["MACRO"],
};
const pdfPosts = Array.from({ length: 20 }, (_, index) => ({
  id: `research-pdf-${index}`, title: `PDF research article ${index}`,
  content: "## Source evidence\n\nResearch from the original PDF remains in the mixed feed.",
  timestamp: new Date(Date.UTC(2026, 9, 16, 12, 0, 20 - index)).toISOString(),
  tags: [index % 2 ? "CREDIT" : "MACRO"],
  source: index === 1 ? { ...source, figures: [] } : source,
}));
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="400"><rect width="800" height="400" fill="white"/><text x="80" y="200" fill="black">Source research chart</text></svg>';

for (const width of [1440, 393]) {
  test(`PDF tab preserves the mixed feed and article controls at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await installClearFixtures(page);
    let postsRequests = 0;
    await page.route("**/api/newsfeed/posts**", route => {
      postsRequests++;
      return route.fulfill({ json: [ordinary, ...pdfPosts] });
    });
    await page.route("**/api/newsfeed/research/files/*.png", route => route.fulfill({ contentType: "image/svg+xml", body: svg }));
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    const main = page.getByTestId("feed-panel-commentary");
    await expect(main.getByText(ordinary.title, { exact: true })).toBeVisible();
    await expect(main.getByText(pdfPosts[0].title, { exact: true })).toBeVisible();
    await main.getByRole("button", { name: "Next page" }).click();
    await expect(main).toContainText("Page 2 of 2");

    const tab = page.getByRole("tab", { name: "PDFs", exact: true });
    await tab.click();
    await expect(tab).toHaveAttribute("aria-selected", "true");
    const panel = page.getByTestId("feed-panel-pdfs");
    await expect(panel.getByTestId("news-feed-item")).toHaveCount(18);
    await expect(panel).toContainText("Showing 1–18 of 20");
    await expect(panel).not.toContainText(ordinary.title);
    const first = panel.getByTestId("news-feed-item").first();
    await expect(first.getByRole("link", { name: "TS Lombard · Source PDF" })).toHaveAttribute("href", pdfUrl);
    await expect(panel.getByTestId("news-feed-item").nth(1)).toContainText("Text-only source evidence");
    await expect(page.getByTestId("feed-rail")).toContainText("PDF research");
    await tab.scrollIntoViewIfNeeded();
    const tablist = page.getByRole("tablist", { name: "Feed source" });
    for (const button of await tablist.getByRole("tab").all()) {
      const box = await button.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`pdf-feed-${width}.png`) });
    await testInfo.attach(`pdf-feed-${width}`, { path: testInfo.outputPath(`pdf-feed-${width}.png`), contentType: "image/png" });

    await first.getByRole("button", { name: "Open lightbox for: PDF research article 0" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("dialog").getByRole("link", { name: "Source PDF", exact: true })).toHaveAttribute("href", pdfUrl);
    await expect(page.getByRole("button", { name: "Previous post" })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await first.getByRole("button", { name: "MACRO", exact: true }).click();
    await expect(panel.getByTestId("news-feed-item")).toHaveCount(10);
    await panel.getByRole("button", { name: "Clear all", exact: true }).click();
    await expect(panel.getByTestId("news-feed-item")).toHaveCount(18);
    await panel.getByRole("button", { name: "Next page" }).click();
    await expect(panel.getByTestId("news-feed-item")).toHaveCount(2);
    const beforeRefresh = postsRequests;
    await page.getByRole("button", { name: "Refresh feed" }).click();
    await expect.poll(() => postsRequests).toBeGreaterThan(beforeRefresh);
    await page.getByRole("tab", { name: "Commentary", exact: true }).click();
    await expect(main.getByText(ordinary.title, { exact: true })).toBeVisible();
    await expect(main.getByText(pdfPosts[0].title, { exact: true })).toBeVisible();
    await expect(main).toContainText("Showing 1–18 of 21");
  });

  test(`PDF empty state leaves Commentary available at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await installClearFixtures(page);
    await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [ordinary] }));
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    await expect(page.getByText(ordinary.title, { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "PDFs", exact: true }).click();
    await expect(page.getByTestId("feed-panel-pdfs")).toHaveText("No PDF articles available.");
    await page.getByRole("tab", { name: "Commentary", exact: true }).click();
    await expect(page.getByText(ordinary.title, { exact: true })).toBeVisible();
  });
}
