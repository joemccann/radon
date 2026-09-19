import { test, expect } from "@playwright/test";

const base = "/api/newsfeed/research/files/";
const source = { kind: "dropbox", publisher: "PNC Economics", url: base + "c".repeat(64) + ".pdf", documentDate: "2026-09-16", folderDate: "2026-09-17",
  pages: [1], figures: [], fileId: "id:fixture", revision: "r1", contentHash: "c".repeat(64) };
const keep = { id: "research-" + "a".repeat(64), title: "FOMC hikes 25 bps to 3.75%-4.00%", content: "The FOMC raised the fed funds rate by 25 basis points.",
  timestamp: "2026-09-19T08:34:00Z", images: [], tags: ["MACRO", "RATES"], source };
const drop = { ...keep, id: "research-" + "b".repeat(64), title: "Bloomberg estimates use only 5 out of a total of 19 analysts on Avolta", tags: ["EQUITIES"] };

for (const width of [1440, 393]) {
  test(`operator votes on research items at ${width}px: down hides, up with want-more stays`, async ({ page }, testInfo) => {
    const votes: unknown[] = [];
    await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [keep, drop] }));
    await page.route("**/api/newsfeed/research/feedback", async route => {
      const body = route.request().postDataJSON() as { postId: string; vote: string };
      votes.push(body);
      await route.fulfill({ json: { id: "v" + votes.length, postId: body.postId, vote: body.vote, hidden: body.vote === "down" } });
    });
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    const avolta = page.locator("article, li, div").filter({ has: page.getByText(drop.title, { exact: true }) }).last();
    await expect(page.getByText(drop.title, { exact: true })).toBeVisible();
    const controls = page.locator("[data-research-feedback]");
    await expect(controls).toHaveCount(2);

    // Thumbs up with "want more" on the FOMC item.
    const first = controls.first();
    await first.getByRole("button", { name: "Thumbs up" }).click();
    await first.getByRole("button", { name: "Want more (chart, detail)" }).click();
    await first.getByLabel("Comment (optional)").fill("add the dot plot chart");
    expect(votes).toHaveLength(0);
    await first.screenshot({ path: testInfo.outputPath(`feedback-up-panel-${width}.png`) });
    await first.getByRole("button", { name: "Save feedback" }).click();
    await expect(first.getByText("Saved")).toBeVisible();
    expect(votes[0]).toEqual({ postId: keep.id, vote: "up", reasons: ["want_more"], comment: "add the dot plot chart" });
    await expect(page.getByText(keep.title, { exact: true })).toBeVisible();

    // Thumbs down on the Avolta item removes it from the feed.
    const second = controls.nth(1);
    await second.getByRole("button", { name: "Thumbs down" }).click();
    await second.getByRole("button", { name: "Not relevant to me" }).click();
    await second.getByLabel("Comment (optional)").fill("single stock, not for me");
    await second.screenshot({ path: testInfo.outputPath(`feedback-down-panel-${width}.png`) });
    await second.getByRole("button", { name: "Save feedback" }).click();
    await expect(page.getByText(drop.title, { exact: true })).toHaveCount(0);
    expect(votes[1]).toEqual({ postId: drop.id, vote: "down", reasons: ["not_relevant"], comment: "single stock, not for me" });
    await expect(avolta).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath(`feed-after-votes-${width}.png`) });
  });
}
