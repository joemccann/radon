import { expect, test, type Page } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";

type Surface = "feedback" | "share" | "columns";
type FadeRecord = { surface: Surface; phase: string; duration: number; curve: string; property: string; inert: boolean; hidden: string | null };
const sourceHash = "c".repeat(64);
const post = {
  id: "research-" + "a".repeat(64), title: "Market data", content: "Sample report.",
  timestamp: "2026-09-04T18:00:00Z", images: [], tags: ["MACRO"],
  source: { kind: "dropbox", publisher: "PNC Economics", url: `/api/newsfeed/research/files/${sourceHash}.pdf`,
    documentDate: "2026-09-04", folderDate: "2026-09-04", pages: [1], figures: [], fileId: "id:fixture", revision: "r1", contentHash: sourceHash },
};

async function setup(page: Page, surface: Surface, stepFrames = false) {
  const token = process.env.RADON_AUTHLESS_TEST_TOKEN;
  if (token) await page.setExtraHTTPHeaders({ "x-radon-authless-test": token });
  await installClearFixtures(page);
  await page.route("**/api/newsfeed/posts**", route => route.fulfill({ json: [post] }));
  await page.routeWebSocket(/\/ws(?:\?|$)|:8765/, socket => {
    socket.send(JSON.stringify({ type: "status", ib_connected: true, market_data_degraded: false, subscriptions: [] }));
  });
  await page.addInitScript(({ stepFrames }) => {
    const records: unknown[] = [];
    Object.assign(window, { __pointerFadeRecords: records });
    document.addEventListener("transitionrun", event => {
      const panel = event.target;
      if (!(panel instanceof HTMLElement) || !panel.matches(".pointer-fade") || event.propertyName !== "opacity") return;
      const style = getComputedStyle(panel);
      records.push({
        surface: panel.closest("[data-research-feedback]") ? "feedback" : panel.closest("[data-newsfeed-share]") ? "share" : "columns",
        phase: panel.dataset.fadeState, duration: Math.round(parseFloat(style.transitionDuration) * 1000),
        curve: style.transitionTimingFunction, property: style.transitionProperty,
        inert: panel.inert, hidden: panel.getAttribute("aria-hidden"),
      });
      if (stepFrames && panel.dataset.fadeState === "open") {
        for (const animation of panel.getAnimations()) {
          animation.pause();
          animation.currentTime = parseFloat(style.transitionDuration) * 100;
        }
      }
    });
  }, { stepFrames });
  await page.goto(surface === "columns" ? "/portfolio" : "/dashboard");
  const root = surface === "columns" ? page.locator(".columns-toggle").first()
    : page.getByTestId("news-feed-item").filter({ hasText: post.title }).locator(surface === "feedback" ? "[data-research-feedback]" : "[data-newsfeed-share]");
  const trigger = root.getByRole("button", { name: surface === "columns" ? /COLUMNS/ : surface === "feedback" ? "Thumbs up" : "Share", exact: surface !== "columns" });
  await expect(trigger).toBeVisible();
  return { root, trigger, panel: root.locator(".pointer-fade") };
}

async function records(page: Page) {
  return page.evaluate(() => (window as unknown as { __pointerFadeRecords: FadeRecord[] }).__pointerFadeRecords);
}

test.describe("pointer opacity presence", () => {
  test.use({ hasTouch: false, isMobile: false, viewport: { width: 1440, height: 1000 } });
  for (const surface of ["feedback", "share", "columns"] as const) {
    test(`${surface}: only opacity transitions; exits are inert and keyboard activation is immediate`, async ({ page }, testInfo) => {
      const { trigger, panel } = await setup(page, surface, true);
      const enter = surface === "columns" ? 125 : 160;
      const exit = surface === "columns" ? 100 : 120;
      await trigger.click();
      await expect.poll(async () => (await records(page)).find(record => record.surface === surface && record.phase === "open")).toEqual({
        surface, phase: "open", duration: enter, curve: "cubic-bezier(0.25, 1, 0.5, 1)", property: "opacity", inert: false, hidden: null,
      });
      const geometry = await panel.boundingBox();
      const firstOpacity = await panel.evaluate(el => Number(getComputedStyle(el).opacity));
      expect(firstOpacity).toBeGreaterThan(0);
      expect(firstOpacity).toBeLessThan(1);
      await page.screenshot({ path: testInfo.outputPath(`${surface}-entry-10pct.png`) });
      await panel.evaluate(el => { for (const animation of el.getAnimations()) animation.currentTime = Number(animation.effect?.getTiming().duration) * 0.5; });
      expect(await panel.evaluate(el => Number(getComputedStyle(el).opacity))).toBeGreaterThan(firstOpacity);
      expect(await panel.boundingBox()).toEqual(geometry);
      await page.screenshot({ path: testInfo.outputPath(`${surface}-entry-50pct.png`) });
      await panel.evaluate(el => { for (const animation of el.getAnimations()) animation.finish(); });
      await expect.poll(() => panel.evaluate(el => getComputedStyle(el).opacity)).toBe("1");
      await page.screenshot({ path: testInfo.outputPath(`${surface}-settled.png`), fullPage: false });
      expect(await panel.boundingBox()).toEqual(geometry);
      await trigger.click();
      await expect.poll(async () => (await records(page)).find(record => record.surface === surface && record.phase === "exiting")).toEqual({
        surface, phase: "exiting", duration: exit, curve: "cubic-bezier(0.25, 1, 0.5, 1)", property: "opacity", inert: true, hidden: "true",
      });
      await expect(panel).toHaveCount(0);

      const pointerEvents = (await records(page)).length;
      await trigger.focus();
      await trigger.press("Enter");
      await expect(panel).toHaveAttribute("data-fade-motion", "instant");
      expect(await panel.evaluate(el => getComputedStyle(el).transitionProperty)).toBe("none");
      expect(await panel.evaluate(el => el.getAnimations().length)).toBe(0);
      const input = panel.locator("textarea, input").first();
      await input.focus();
      await input.press("Escape");
      await expect(panel).toHaveCount(0);
      await expect(trigger).toBeFocused();
      expect((await records(page)).length).toBe(pointerEvents);
    });

    test(`${surface}: reduced-motion pointer activation stays immediate`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      const { trigger, panel } = await setup(page, surface);
      await trigger.click();
      await expect(panel).toHaveAttribute("data-fade-motion", "instant");
      expect(await panel.evaluate(el => el.getAnimations().length)).toBe(0);
      await trigger.click();
      await expect(panel).toHaveCount(0);
      expect(await records(page)).toEqual([]);
    });
  }
});

for (const width of [393, 1024]) test.describe(`touch opacity presence at ${width}px`, () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width, height: 900 } });
  for (const surface of (width === 393 ? ["feedback", "share"] : ["feedback", "share", "columns"]) as Surface[]) {
    test(`${surface}: a real tap starts the audited fade`, async ({ page }) => {
      const { trigger, panel } = await setup(page, surface);
      await trigger.tap();
      await expect(panel).toHaveAttribute("data-fade-motion", "pointer");
      await expect.poll(async () => (await records(page)).find(record => record.phase === "open")?.duration).toBe(surface === "columns" ? 125 : 160);
      await expect.poll(() => panel.evaluate(el => getComputedStyle(el).opacity)).toBe("1");
      await trigger.tap();
      await expect(panel).toHaveCount(0);
    });
  }
});
