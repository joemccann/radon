import { test, expect } from "@playwright/test";

test.describe("dashboard feed tabs", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/newsfeed/posts**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "p1",
            title: "Commentary fixture",
            content: "Body",
            timestamp: new Date().toISOString(),
            images: [],
            tags: ["MACRO"],
          },
        ]),
      }),
    );
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
    await expect(page.getByTestId("feed-panel-headlines")).toBeVisible();
    expect(leaked).toEqual([]);
  });
});
