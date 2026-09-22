import { expect, test } from "@playwright/test";

test.describe("demo dashboard headlines", () => {
  test.skip(process.env.NEXT_PUBLIC_RADON_DEMO !== "1", "demo-only transport");

  // Window-relative so the fixture never rots into the past as the calendar
  // advances (T-437/T-455) - a pinned date silently ages out of any
  // freshness-sensitive rendering.
  const HEADLINE_TIME = new Date(Date.now() - 60 * 1000).toISOString();

  test("renders the current snapshot without dialing a WebSocket", async ({ page }) => {
    const websocketUrls: string[] = [];
    let snapshotRequests = 0;
    page.on("websocket", (socket) => websocketUrls.push(socket.url()));
    await page.route("**/api/headlines", (route) => {
      snapshotRequests += 1;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [{
            kind: "headline",
            id: "demo-browser-headline",
            time: HEADLINE_TIME,
            important: true,
            content: "Demo headline snapshot reached the dashboard.",
            impact: [{ symbol: "SPX", impact: "bullish" }],
          }],
        }),
      });
    });
    await page.route("**/api/newsfeed/posts**", (route) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    }));

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/dashboard");
    await page.getByRole("tab", { name: "Headlines" }).click();

    await expect(page.getByText("Demo headline snapshot reached the dashboard.")).toBeVisible();
    await expect(page.getByText("Headlines feed unavailable.")).toHaveCount(0);
    await expect(page.getByText("flash", { exact: true })).toBeVisible();
    expect(snapshotRequests).toBe(1);
    expect(websocketUrls.filter((url) => url.includes("/ws-headlines"))).toEqual([]);

    const screenshotPath = process.env.DEMO_HEADLINES_SHOT_PATH;
    if (screenshotPath) await page.screenshot({ path: screenshotPath, fullPage: true });
  });
});
