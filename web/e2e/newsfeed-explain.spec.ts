import { test, expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const SHOTS = path.join(process.cwd(), "..", "docs", "design-shots", "explain-button");

const pjm = {
  id: "pjm-2028-capacity",
  title: "PJM 2028/2029 Capacity Auction Cleared at $325/MW-Day Cap, 6.8 GW Short",
  content:
    "The 2028/2029 PJM capacity auction results posted July 14, 2026 cleared at the cap of $325/MW-day. The auction came up 6.8 GW short of its reliability requirement. Without the collar, the uncapped price would have been $555/MW-day.",
  timestamp: "2026-10-17T18:37:00Z",
  images: [] as string[],
  tags: ["POWER-MARKETS", "ENERGY", "MARKET-STRUCTURE", "MACRO"],
};

const chart = {
  ...pjm,
  id: "pjm-chart",
  title: "PJM capacity stack with collar",
  content:
    "The stacked chart shows cleared megawatts under the $325 collar and the 6.8 GW reliability gap after the 2028/2029 auction.",
  images: ["https://media.radon.run/images/pjm-chart.png"],
};

const explanation =
  "PJM paid power plants to stay available in 2028 and 2029. A capacity auction buys that standby power, priced in dollars per megawatt-day. The price hit the $325 cap and the region was still 6.8 gigawatts short. This can raise capacity charges on power bills.";

const svg =
  '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675"><rect width="1200" height="675" fill="#101714"/><text x="40" y="80" fill="#e8eee9" font-size="28">Synthetic PJM stack</text></svg>';

async function stubFeed(page: Page, delayMs = 0) {
  await page.addInitScript(() => {
    const style = document.createElement("style");
    style.textContent = "#radon-toast-viewport,.toast{display:none!important}";
    document.documentElement.appendChild(style);
  });
  await page.route("**/api/newsfeed/posts**", (route) =>
    route.fulfill({ json: [pjm, chart] }),
  );
  await page.route("**/api/newsfeed/explain", async (route) => {
    if (delayMs) await page.waitForTimeout(delayMs);
    const frames = [
      "event: stage\ndata: {\"stage\":\"queued\",\"budgetMs\":90000}\n\n",
      "event: stage\ndata: {\"stage\":\"drafting\"}\n\n",
      `event: result\ndata: ${JSON.stringify({
        explanation,
        cached: false,
        thin: false,
        model: "fixture",
        provider: "fixture",
        hasImage: route.request().postDataJSON()?.imageUrls?.length > 0,
      })}\n\n`,
    ].join("");
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream; charset=utf-8",
      headers: { "cache-control": "no-store" },
      body: frames,
    });
  });
  await page.route("https://media.radon.run/**", (route) =>
    route.fulfill({ contentType: "image/svg+xml", body: svg }),
  );
}

for (const width of [1440, 393] as const) {
  const height = width === 393 ? 852 : 900;
  test(`explain button idle loading and explained at ${width}px`, async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    await mkdir(SHOTS, { recursive: true });
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: "dark" });
    await stubFeed(page, 400);
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    await page.addStyleTag({
      content: "#radon-toast-viewport,.toast-container,nextjs-portal,[data-nextjs-toast]{display:none!important}",
    });
    const hideChrome = async () => {
      await page.evaluate(() => {
        document.getElementById("radon-toast-viewport")?.remove();
        document.querySelectorAll("nextjs-portal").forEach((node) => node.remove());
      });
    };
    const textCard = page.getByTestId("news-feed-item").filter({ hasText: "MW-Day Cap" });
    const imageCard = page.getByTestId("news-feed-item").filter({ hasText: "capacity stack with collar" });
    await expect(textCard.getByRole("button", { name: "Explain" })).toBeVisible();
    await expect(imageCard.getByRole("button", { name: "Explain" })).toBeVisible();
    const idlePath = path.join(SHOTS, `idle-${width}.png`);
    await hideChrome();
    await textCard.screenshot({ path: idlePath });
    await testInfo.attach(`idle-${width}`, { path: idlePath, contentType: "image/png" });

    const pending = textCard.getByRole("button", { name: /Explain/ });
    await pending.click();
    await expect(textCard.getByTestId("newsfeed-explain")).toHaveAttribute("data-state", "loading");
    const loadingPath = path.join(SHOTS, `loading-${width}.png`);
    await hideChrome();
    await textCard.screenshot({ path: loadingPath });
    await testInfo.attach(`loading-${width}`, { path: loadingPath, contentType: "image/png" });

    await expect(textCard.getByText(/standby power/)).toBeVisible();
    await expect(textCard.getByTestId("newsfeed-explain")).toHaveAttribute("data-state", "explained");
    await expect(textCard.getByRole("button", { name: "Hide explanation" })).toBeVisible();
    const explainedPath = path.join(SHOTS, `explained-text-${width}.png`);
    await hideChrome();
    await textCard.screenshot({ path: explainedPath });
    await testInfo.attach(`explained-text-${width}`, { path: explainedPath, contentType: "image/png" });

    await imageCard.getByRole("button", { name: "Explain" }).click();
    const imagePanel = imageCard.getByRole("region", { name: "Explanation" });
    await expect(imagePanel.getByText(/standby power/)).toBeVisible();
    await imagePanel.scrollIntoViewIfNeeded();
    const imagePath = path.join(SHOTS, `explained-image-${width}.png`);
    await hideChrome();
    await imageCard.screenshot({ path: imagePath });
    await testInfo.attach(`explained-image-${width}`, { path: imagePath, contentType: "image/png" });
    await expect(textCard).not.toContainText("—");
    await expect(imageCard).not.toContainText("—");
    await expect(textCard).not.toContainText(/investment advice|financial advice/i);
    await expect(imageCard).not.toContainText(/investment advice|financial advice/i);
  });
}
