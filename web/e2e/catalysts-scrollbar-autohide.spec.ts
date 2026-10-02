import { expect, test } from "@playwright/test";

/**
 * Catalysts: an expanded category scrolls inside a 320px box. Its scrollbar
 * must be invisible at rest and appear only while the list scrolls or is
 * hovered (2026-09-29 report: a permanent grey bar over the ET column).
 *
 * Layout-only: load the dashboard for the production sheet, then inject an
 * expanded list. `is-scrolling` is the class CatalystsQuadrant toggles on
 * scroll (unit-tested in catalysts-quadrant.test.tsx).
 */

test("the catalysts scrollbar is hidden at rest and shown while scrolling or hovered", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.waitForLoadState("domcontentloaded");

  await page.evaluate(() => {
    const host = document.createElement("div");
    host.id = "catalyst-probe";
    host.style.cssText = "position:fixed;top:40px;left:40px;width:520px;z-index:100;background:var(--bg-panel)";
    const rows = Array.from({ length: 20 }, (_, i) =>
      `<div class="catalyst-group__row"><span class="catalyst-group__name">Print ${i}</span><span class="catalyst-group__when">30 Sept 08:30 ET</span></div>`,
    ).join("");
    host.innerHTML = `<div class="catalyst-group"><div class="catalyst-group__rows catalyst-group__rows--scroll">${rows}</div></div>`;
    document.body.appendChild(host);
  });

  const list = page.locator("#catalyst-probe .catalyst-group__rows--scroll");
  // Computed scrollbar-color; the thumb (first value) is what shows.
  const thumb = () => list.evaluate((el) => getComputedStyle(el).scrollbarColor.split(/ (?=rgb)/)[0]);
  const HIDDEN = "rgba(0, 0, 0, 0)";
  await page.mouse.move(1300, 800);

  expect(await list.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
  expect(await thumb()).toBe(HIDDEN);
  const rest = testInfo.outputPath("catalysts-scrollbar-rest.png");
  await list.screenshot({ path: rest });
  await testInfo.attach("rest", { path: rest, contentType: "image/png" });

  await list.evaluate((el) => el.classList.add("is-scrolling"));
  expect(await thumb()).not.toBe(HIDDEN);
  const scrolling = testInfo.outputPath("catalysts-scrollbar-scrolling.png");
  await list.screenshot({ path: scrolling });
  await testInfo.attach("scrolling", { path: scrolling, contentType: "image/png" });

  await list.evaluate((el) => el.classList.remove("is-scrolling"));
  await list.hover();
  expect(await thumb()).not.toBe(HIDDEN);
});
