import { test, expect } from "@playwright/test";

const base = "/api/newsfeed/research/files/";
const source = { kind: "dropbox", publisher: "PNC Economics", url: base + "c".repeat(64) + ".pdf", documentDate: "2026-09-16", folderDate: "2026-09-17",
  pages: [1], figures: [], fileId: "id:fixture", revision: "r1", contentHash: "c".repeat(64) };
const post = { id: "research-" + "a".repeat(64), title: "FOMC hikes 25 bps to 3.75%-4.00%", content: "The FOMC raised the fed funds rate.",
  timestamp: "2026-09-19T08:34:00Z", images: [], tags: ["MACRO"], source };
const KEY = "d".repeat(64);
const held = [
  { workKey: KEY, fileName: "jpm_flows___liquidity.pdf", publisher: "J.P. Morgan", series: "jpm flows liquidity", docType: "research", folderDate: "2026-09-17",
    documentDate: "2026-09-16", outcome: "held", reasonCodes: ["NUMBER_NOT_ON_PAGE"],
    drafts: [{ title: "Tech bond issuance adds 10-20bp to global yields", content: "J.P. Morgan estimates the $260bn increase in net tech issuance adds 10-20bp.", held: "NUMBER_NOT_ON_PAGE", detail: "10-20bp" }] },
  { workKey: "e".repeat(64), fileName: "gbpusd_en_1666701.pdf", publisher: "UBS", series: "gbpusd", docType: "fx_pair_note", folderDate: "2026-09-17",
    documentDate: "2026-09-17", outcome: "dropped", reasonCodes: ["DOC_TYPE_FX_PAIR_NOTE"], drafts: [] },
];

for (const width of [1440, 393]) {
  test(`operator reviews held research at ${width}px and marks one should-have-published`, async ({ page }, testInfo) => {
    const votes: unknown[] = [];
    await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [post] }));
    await page.route("**/api/newsfeed/research/held", route => route.fulfill({ json: { items: held, pending: 34 } }));
    const decisions: unknown[] = [];
    await page.route("**/api/newsfeed/research/rules", async route => {
      if (route.request().method() === "POST") {
        decisions.push(route.request().postDataJSON());
        return route.fulfill({ json: { id: "series_deny:ubs cio fx view", status: "approved" } });
      }
      return route.fulfill({ json: { proposed: [{ id: "series_deny:ubs cio fx view", kind: "series_deny", key: "ubs cio fx view", downs: 4, ups: 0, evidence: 4 }], approved: [] } });
    });
    await page.route("**/api/newsfeed/research/feedback", async route => {
      votes.push(route.request().postDataJSON());
      await route.fulfill({ json: { id: "v1", workKey: KEY, vote: "up" } });
    });
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    await page.getByTestId("feed-tab-held").click();
    const panel = page.getByTestId("feed-panel-held");
    await expect(panel.getByText("2 of 34 awaiting review")).toBeVisible();
    const card = panel.locator("li").filter({ hasText: "jpm_flows___liquidity.pdf" }).first();
    await expect(card.getByLabel("Reasons").getByText("Number not on the cited page")).toBeVisible();
    await card.locator("summary").click();
    await expect(card.getByText("10-20bp", { exact: true })).toBeVisible();
    await panel.screenshot({ path: testInfo.outputPath(`held-review-${width}.png`) });

    await card.getByRole("button", { name: "Should have published" }).click();
    await card.getByLabel("Comment (optional)").fill("the range is 10-15bp, it is in the first bullet");
    expect(votes).toHaveLength(0);
    await card.getByRole("button", { name: "Save feedback" }).click();
    await expect(panel.getByText("jpm_flows___liquidity.pdf")).toHaveCount(0);
    expect(votes[0]).toEqual({ workKey: KEY, vote: "up", reasons: [], comment: "the range is 10-15bp, it is in the first bullet" });
    await expect(panel.getByText("gbpusd_en_1666701.pdf")).toBeVisible();

    // A proposed triage rule is only a proposal until the operator approves it.
    const proposal = panel.getByRole("list", { name: "Proposed rules" }).locator("li").first();
    await expect(proposal.getByText("You rejected 4 of its items and approved none.")).toBeVisible();
    expect(decisions).toHaveLength(0);
    await proposal.getByRole("button", { name: "Approve rule" }).click();
    await expect(panel.getByRole("list", { name: "Active rules" }).getByText("Stop reviewing the series “ubs cio fx view”")).toBeVisible();
    expect(decisions).toEqual([{ id: "series_deny:ubs cio fx view", decision: "approve" }]);
    await panel.screenshot({ path: testInfo.outputPath(`held-rules-${width}.png`) });
  });
}

test("the Held tab is absent when the feed carries no research provenance", async ({ page }) => {
  await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [{ ...post, id: "market-ear-1", source: undefined }] }));
  await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("feed-tab-commentary")).toBeVisible();
  await expect(page.getByTestId("feed-tab-held")).toHaveCount(0);
});
